"""Tests for the nftables firewall parser (core/firewall.py).

All tests are Qt-free — they exercise the pure-Python parse path using
JSON fixtures that mirror real ``nft -j list ruleset`` output.
"""

from __future__ import annotations

import json

import pytest

from netgrip.core.firewall import (
    NFT_COMMAND,
    _extract_ifaces,
    _render_exprs,
    parse_ruleset,
)
from netgrip.core.model import NftRuleset

# ---------------------------------------------------------------------------
# Fixtures — trimmed but structurally faithful nft -j list ruleset output
# ---------------------------------------------------------------------------

# Minimal: one inet table, three base chains, a handful of rules.
FIXTURE_BASIC: dict = {
    "nftables": [
        {"metainfo": {"version": "1.0.9", "json_schema_version": 1}},
        {"table": {"family": "inet", "name": "filter", "handle": 1}},
        {
            "chain": {
                "family": "inet", "table": "filter", "name": "INPUT",
                "handle": 1, "type": "filter", "hook": "input", "prio": 0,
                "policy": "drop",
            }
        },
        {
            "chain": {
                "family": "inet", "table": "filter", "name": "FORWARD",
                "handle": 2, "type": "filter", "hook": "forward", "prio": 0,
                "policy": "drop",
            }
        },
        {
            "chain": {
                "family": "inet", "table": "filter", "name": "OUTPUT",
                "handle": 3, "type": "filter", "hook": "output", "prio": 0,
                "policy": "accept",
            }
        },
        # Allow loopback
        {
            "rule": {
                "family": "inet", "table": "filter", "chain": "INPUT",
                "handle": 4,
                "expr": [
                    {"match": {
                        "op": "==",
                        "left": {"meta": {"key": "iifname"}},
                        "right": "lo",
                    }},
                    {"accept": None},
                ],
            }
        },
        # Allow established/related
        {
            "rule": {
                "family": "inet", "table": "filter", "chain": "INPUT",
                "handle": 5,
                "expr": [
                    {"match": {
                        "op": "in",
                        "left": {"ct": {"key": "state"}},
                        "right": {"set": ["established", "related"]},
                    }},
                    {"accept": None},
                ],
            }
        },
        # Allow SSH on eth0
        {
            "rule": {
                "family": "inet", "table": "filter", "chain": "INPUT",
                "handle": 6,
                "expr": [
                    {"match": {
                        "op": "==",
                        "left": {"meta": {"key": "iifname"}},
                        "right": "eth0",
                    }},
                    {"match": {
                        "op": "==",
                        "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                        "right": 22,
                    }},
                    {"accept": None},
                ],
            }
        },
        # Drop everything else (counter + drop)
        {
            "rule": {
                "family": "inet", "table": "filter", "chain": "INPUT",
                "handle": 7,
                "expr": [
                    {"counter": {"packets": 0, "bytes": 0}},
                    {"drop": None},
                ],
            }
        },
        # Forward rule with oifname
        {
            "rule": {
                "family": "inet", "table": "filter", "chain": "FORWARD",
                "handle": 8,
                "expr": [
                    {"match": {
                        "op": "==",
                        "left": {"meta": {"key": "oifname"}},
                        "right": "eth0",
                    }},
                    {"accept": None},
                ],
            }
        },
    ]
}

# NAT table fixture
FIXTURE_NAT: dict = {
    "nftables": [
        {"table": {"family": "ip", "name": "nat", "handle": 2}},
        {
            "chain": {
                "family": "ip", "table": "nat", "name": "POSTROUTING",
                "handle": 1, "type": "nat", "hook": "postrouting", "prio": 100,
                "policy": "accept",
            }
        },
        {
            "rule": {
                "family": "ip", "table": "nat", "chain": "POSTROUTING",
                "handle": 2,
                "expr": [
                    {"match": {
                        "op": "==",
                        "left": {"meta": {"key": "oifname"}},
                        "right": "wan0",
                    }},
                    {"masquerade": None},
                ],
            }
        },
    ]
}

# Empty ruleset (just metainfo)
FIXTURE_EMPTY: dict = {
    "nftables": [
        {"metainfo": {"version": "1.0.9", "json_schema_version": 1}},
    ]
}


# ---------------------------------------------------------------------------
# parse_ruleset: structural tests
# ---------------------------------------------------------------------------

def test_parse_basic_table_count():
    rs = parse_ruleset(FIXTURE_BASIC)
    assert len(rs.tables) == 1
    assert rs.tables[0].name == "filter"
    assert rs.tables[0].family == "inet"


def test_parse_basic_chain_count():
    rs = parse_ruleset(FIXTURE_BASIC)
    table = rs.tables[0]
    assert len(table.chains) == 3
    chain_names = {c.name for c in table.chains}
    assert chain_names == {"INPUT", "FORWARD", "OUTPUT"}


def test_parse_chain_metadata():
    rs = parse_ruleset(FIXTURE_BASIC)
    table = rs.tables[0]
    input_chain = next(c for c in table.chains if c.name == "INPUT")
    assert input_chain.hook == "input"
    assert input_chain.policy == "drop"
    assert input_chain.type == "filter"
    assert input_chain.prio == 0


def test_parse_rule_count_per_chain():
    rs = parse_ruleset(FIXTURE_BASIC)
    table = rs.tables[0]
    input_chain = next(c for c in table.chains if c.name == "INPUT")
    forward_chain = next(c for c in table.chains if c.name == "FORWARD")
    output_chain = next(c for c in table.chains if c.name == "OUTPUT")
    assert len(input_chain.rules) == 4
    assert len(forward_chain.rules) == 1
    assert len(output_chain.rules) == 0


def test_parse_rule_handles():
    rs = parse_ruleset(FIXTURE_BASIC)
    table = rs.tables[0]
    input_chain = next(c for c in table.chains if c.name == "INPUT")
    handles = [r.handle for r in input_chain.rules]
    assert handles == [4, 5, 6, 7]


def test_parse_empty_ruleset():
    rs = parse_ruleset(FIXTURE_EMPTY)
    assert rs.tables == []
    assert rs.all_rules() == []


def test_parse_nat_table():
    rs = parse_ruleset(FIXTURE_NAT)
    assert len(rs.tables) == 1
    table = rs.tables[0]
    assert table.family == "ip"
    assert table.name == "nat"
    chain = table.chains[0]
    assert chain.hook == "postrouting"
    assert chain.type == "nat"
    assert len(chain.rules) == 1


def test_parse_accepts_json_string():
    """parse_ruleset should accept a raw JSON string as well as a dict."""
    rs = parse_ruleset(json.dumps(FIXTURE_BASIC))
    assert len(rs.tables) == 1


def test_parse_invalid_json_raises():
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_ruleset("this is not json {")


def test_parse_wrong_schema_raises():
    with pytest.raises(ValueError, match="nftables"):
        parse_ruleset({"not_nftables": []})


# ---------------------------------------------------------------------------
# Expression rendering
# ---------------------------------------------------------------------------

def test_render_accept():
    assert _render_exprs([{"accept": None}]) == "accept"


def test_render_drop():
    assert _render_exprs([{"drop": None}]) == "drop"


def test_render_match_iifname():
    exprs = [
        {"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": "eth0"}},
        {"accept": None},
    ]
    result = _render_exprs(exprs)
    assert "iifname" in result
    assert "eth0" in result
    assert "accept" in result


def test_render_match_tcp_dport():
    exprs = [
        {"match": {
            "op": "==",
            "left": {"payload": {"protocol": "tcp", "field": "dport"}},
            "right": 443,
        }},
        {"accept": None},
    ]
    result = _render_exprs(exprs)
    assert "tcp" in result
    assert "dport" in result
    assert "443" in result


def test_render_counter():
    exprs = [{"counter": {"packets": 10, "bytes": 1024}}, {"drop": None}]
    result = _render_exprs(exprs)
    assert "counter" in result
    assert "10" in result
    assert "1024" in result


def test_render_masquerade():
    exprs = [{"masquerade": None}]
    assert _render_exprs(exprs) == "masquerade"


def test_render_ct_state():
    exprs = [
        {"match": {
            "op": "in",
            "left": {"ct": {"key": "state"}},
            "right": {"set": ["established", "related"]},
        }},
        {"accept": None},
    ]
    result = _render_exprs(exprs)
    assert "ct" in result
    assert "state" in result


def test_render_log_with_prefix():
    exprs = [{"log": {"prefix": "DROP: "}}]
    result = _render_exprs(exprs)
    assert "log" in result
    assert "DROP" in result


def test_render_limit():
    exprs = [{"limit": {"rate": 10, "per": "second", "burst": 20}}, {"accept": None}]
    result = _render_exprs(exprs)
    assert "limit rate 10/second" in result
    assert "burst 20 packets" in result


# ---------------------------------------------------------------------------
# Interface name extraction
# ---------------------------------------------------------------------------

def test_extract_iifname():
    exprs = [
        {"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": "eth0"}},
    ]
    assert _extract_ifaces(exprs) == ["eth0"]


def test_extract_oifname():
    exprs = [
        {"match": {"op": "==", "left": {"meta": {"key": "oifname"}}, "right": "wan0"}},
    ]
    assert _extract_ifaces(exprs) == ["wan0"]


def test_extract_iface_set():
    exprs = [
        {"match": {
            "op": "==",
            "left": {"meta": {"key": "iifname"}},
            "right": {"set": ["eth0", "eth1"]},
        }},
    ]
    assert set(_extract_ifaces(exprs)) == {"eth0", "eth1"}


def test_extract_no_iface_match():
    exprs = [
        {"match": {
            "op": "==",
            "left": {"payload": {"protocol": "tcp", "field": "dport"}},
            "right": 22,
        }},
        {"accept": None},
    ]
    assert _extract_ifaces(exprs) == []


def test_extract_ignores_non_match_exprs():
    exprs = [{"counter": {"packets": 0, "bytes": 0}}, {"drop": None}]
    assert _extract_ifaces(exprs) == []


# ---------------------------------------------------------------------------
# NftRuleset query methods
# ---------------------------------------------------------------------------

def test_all_rules_flat():
    rs = parse_ruleset(FIXTURE_BASIC)
    rules = rs.all_rules()
    # 4 INPUT rules + 1 FORWARD rule = 5 total
    assert len(rules) == 5


def test_rules_for_iface_eth0():
    rs = parse_ruleset(FIXTURE_BASIC)
    eth0_rules = rs.rules_for_iface("eth0")
    # Handle 6 (INPUT) and handle 8 (FORWARD) reference eth0
    assert len(eth0_rules) == 2
    assert {r.handle for r in eth0_rules} == {6, 8}


def test_rules_for_iface_lo():
    rs = parse_ruleset(FIXTURE_BASIC)
    lo_rules = rs.rules_for_iface("lo")
    assert len(lo_rules) == 1
    assert lo_rules[0].handle == 4


def test_rules_for_unknown_iface_empty():
    rs = parse_ruleset(FIXTURE_BASIC)
    assert rs.rules_for_iface("nonexistent0") == []


def test_chains_for_iface_eth0():
    rs = parse_ruleset(FIXTURE_BASIC)
    chains = rs.chains_for_iface("eth0")
    # Both INPUT and FORWARD have rules for eth0
    chain_names = {c.name for c in chains}
    assert chain_names == {"INPUT", "FORWARD"}


def test_chains_for_iface_deduplication():
    """A chain appears only once even if it has multiple rules for the iface."""
    rs = parse_ruleset(FIXTURE_BASIC)
    chains = rs.chains_for_iface("eth0")
    # No duplicates
    names = [c.name for c in chains]
    assert len(names) == len(set(names))


def test_rules_for_iface_nat():
    rs = parse_ruleset(FIXTURE_NAT)
    wan_rules = rs.rules_for_iface("wan0")
    assert len(wan_rules) == 1
    assert "masquerade" in wan_rules[0].expr_str


# ---------------------------------------------------------------------------
# NFT_COMMAND sanity
# ---------------------------------------------------------------------------

def test_nft_command_shape():
    assert NFT_COMMAND == ["nft", "-j", "list", "ruleset"]
