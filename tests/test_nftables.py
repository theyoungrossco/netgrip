"""Tests for nftables probe parsing and rule-edit action plans."""

from __future__ import annotations

from netgrip.core import actions
from netgrip.core.demo import demo_firewall
from netgrip.core.model import FirewallState
from netgrip.core.probe import (
    _nft_extract_ifaces,
    _nft_summarise_expr,
    parse_nft_json,
    probe_firewall,
)
from netgrip.core.runner import DemoRunner

# ---------------------------------------------------------------------------
# Minimal but structurally faithful `nft -j list ruleset` fixture
# ---------------------------------------------------------------------------

NFT_FIXTURE: dict = {
    "nftables": [
        {"metainfo": {"version": "1.0.6", "release_name": "Lester Gooch",
                     "json_schema_version": 1}},
        {"table": {"family": "inet", "name": "filter", "handle": 1}},
        {
            "chain": {
                "family": "inet", "table": "filter", "name": "INPUT", "handle": 1,
                "type": "filter", "hook": "input", "prio": 0, "policy": "drop",
            }
        },
        {
            "rule": {
                "family": "inet", "table": "filter", "chain": "INPUT", "handle": 2,
                "expr": [
                    {"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": "lo"}},
                    {"accept": None},
                ],
            }
        },
        {
            "rule": {
                "family": "inet", "table": "filter", "chain": "INPUT", "handle": 3,
                "comment": "allow established",
                "expr": [
                    {
                        "match": {
                            "op": "in",
                            "left": {"ct": {"key": "state"}},
                            "right": {"set": ["established", "related"]},
                        }
                    },
                    {"accept": None},
                ],
            }
        },
        {
            "rule": {
                "family": "inet", "table": "filter", "chain": "INPUT", "handle": 4,
                "expr": [
                    {
                        "match": {
                            "op": "==",
                            "left": {"meta": {"key": "iifname"}},
                            "right": {"set": ["eth0", "eth1"]},
                        }
                    },
                    {"drop": None},
                ],
            }
        },
        {
            "chain": {
                "family": "inet", "table": "filter", "name": "OUTPUT", "handle": 2,
                "type": "filter", "hook": "output", "prio": 0, "policy": "accept",
            }
        },
        {"table": {"family": "ip", "name": "nat", "handle": 2}},
        {
            "chain": {
                "family": "ip", "table": "nat", "name": "POSTROUTING", "handle": 1,
                "type": "nat", "hook": "postrouting", "prio": 100, "policy": "accept",
            }
        },
        {
            "rule": {
                "family": "ip", "table": "nat", "chain": "POSTROUTING", "handle": 5,
                "expr": [
                    {"match": {"op": "==", "left": {"meta": {"key": "oifname"}}, "right": "eth0"}},
                    {"masquerade": None},
                ],
            }
        },
    ]
}


# ---------------------------------------------------------------------------
# parse_nft_json
# ---------------------------------------------------------------------------

def test_parse_nft_json_tables():
    state = parse_nft_json(NFT_FIXTURE)
    assert state.available is True
    names = {(t.family, t.name) for t in state.tables}
    assert ("inet", "filter") in names
    assert ("ip", "nat") in names


def test_parse_nft_json_chains():
    state = parse_nft_json(NFT_FIXTURE)
    inet_filter = next(t for t in state.tables if t.name == "filter")
    chain_names = {c.name for c in inet_filter.chains}
    assert "INPUT" in chain_names
    assert "OUTPUT" in chain_names
    input_chain = next(c for c in inet_filter.chains if c.name == "INPUT")
    assert input_chain.hook == "input"
    assert input_chain.policy == "drop"
    assert input_chain.is_base_chain is True


def test_parse_nft_json_rules_count():
    state = parse_nft_json(NFT_FIXTURE)
    inet_filter = next(t for t in state.tables if t.name == "filter")
    input_chain = next(c for c in inet_filter.chains if c.name == "INPUT")
    assert len(input_chain.rules) == 3  # lo-accept, established-accept, set-drop


def test_parse_nft_json_rule_comment():
    state = parse_nft_json(NFT_FIXTURE)
    inet_filter = next(t for t in state.tables if t.name == "filter")
    input_chain = next(c for c in inet_filter.chains if c.name == "INPUT")
    rule = next(r for r in input_chain.rules if r.handle == 3)
    assert rule.comment == "allow established"


def test_parse_nft_json_iface_extraction_single():
    state = parse_nft_json(NFT_FIXTURE)
    inet_filter = next(t for t in state.tables if t.name == "filter")
    input_chain = next(c for c in inet_filter.chains if c.name == "INPUT")
    lo_rule = next(r for r in input_chain.rules if r.handle == 2)
    assert lo_rule.ifaces == ["lo"]


def test_parse_nft_json_iface_extraction_set():
    state = parse_nft_json(NFT_FIXTURE)
    inet_filter = next(t for t in state.tables if t.name == "filter")
    input_chain = next(c for c in inet_filter.chains if c.name == "INPUT")
    set_rule = next(r for r in input_chain.rules if r.handle == 4)
    assert "eth0" in set_rule.ifaces
    assert "eth1" in set_rule.ifaces


def test_parse_nft_json_oifname_extraction():
    state = parse_nft_json(NFT_FIXTURE)
    nat = next(t for t in state.tables if t.name == "nat")
    postrouting = next(c for c in nat.chains if c.name == "POSTROUTING")
    rule = postrouting.rules[0]
    assert "eth0" in rule.ifaces


def test_parse_nft_json_rules_for_iface():
    state = parse_nft_json(NFT_FIXTURE)
    eth0_rules = state.rules_for_iface("eth0")
    # Should find the set-drop rule in INPUT and the masquerade rule in nat POSTROUTING
    assert len(eth0_rules) >= 2


def test_parse_nft_json_chains_for_iface():
    state = parse_nft_json(NFT_FIXTURE)
    pairs = state.chains_for_iface("eth0")
    chain_names = {c.name for _, c in pairs}
    assert "INPUT" in chain_names
    assert "POSTROUTING" in chain_names


def test_parse_nft_json_empty_ruleset():
    state = parse_nft_json({"nftables": [{"metainfo": {}}]})
    assert state.available is True
    assert state.tables == []


def test_parse_nft_json_missing_key():
    state = parse_nft_json({})
    assert state.available is True
    assert state.tables == []


# ---------------------------------------------------------------------------
# probe_firewall — error handling
# ---------------------------------------------------------------------------

class _ErrorRunner:
    """Stub runner that raises RuntimeError on every command."""
    label = "error"

    def run(self, _argv):
        raise RuntimeError("nft not found")

    def run_privileged(self, _script):
        raise RuntimeError("nft not found")


def test_probe_firewall_absent_nft_returns_unavailable():
    state = probe_firewall(_ErrorRunner())  # type: ignore[arg-type]
    assert state.available is False
    assert state.tables == []


def test_probe_firewall_demo_runner():
    """DemoRunner has no nft; probe_firewall must not raise."""
    runner = DemoRunner()
    state = probe_firewall(runner)
    # DemoRunner will raise since nft isn't a demo command — must return gracefully.
    assert isinstance(state, FirewallState)


# ---------------------------------------------------------------------------
# _nft_extract_ifaces
# ---------------------------------------------------------------------------

def test_extract_ifaces_single_match():
    expr = [{"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": "eth0"}}]
    assert _nft_extract_ifaces(expr) == ["eth0"]


def test_extract_ifaces_oifname():
    expr = [{"match": {"op": "==", "left": {"meta": {"key": "oifname"}}, "right": "wan0"}}]
    assert _nft_extract_ifaces(expr) == ["wan0"]


def test_extract_ifaces_set():
    expr = [
        {
            "match": {
                "op": "==",
                "left": {"meta": {"key": "iifname"}},
                "right": {"set": ["eth0", "eth1"]},
            }
        }
    ]
    assert _nft_extract_ifaces(expr) == ["eth0", "eth1"]


def test_extract_ifaces_no_meta_match():
    # ct state match — should not extract any iface
    expr = [
        {
            "match": {
                "op": "in",
                "left": {"ct": {"key": "state"}},
                "right": {"set": ["established", "related"]},
            }
        }
    ]
    assert _nft_extract_ifaces(expr) == []


def test_extract_ifaces_empty():
    assert _nft_extract_ifaces([{"accept": None}]) == []


# ---------------------------------------------------------------------------
# _nft_summarise_expr
# ---------------------------------------------------------------------------

def test_summarise_accept():
    assert _nft_summarise_expr([{"accept": None}]) == "accept"


def test_summarise_drop():
    assert _nft_summarise_expr([{"drop": None}]) == "drop"


def test_summarise_masquerade():
    assert _nft_summarise_expr([{"masquerade": None}]) == "masquerade"


def test_summarise_jump():
    assert _nft_summarise_expr([{"jump": {"target": "mychain"}}]) == "jump mychain"


def test_summarise_match_iifname():
    expr = [
        {"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": "eth0"}},
        {"accept": None},
    ]
    summary = _nft_summarise_expr(expr)
    assert "iifname" in summary
    assert "eth0" in summary
    assert "accept" in summary


def test_summarise_counter_skipped():
    expr = [
        {"counter": {"packets": 100, "bytes": 4096}},
        {"accept": None},
    ]
    assert "counter" not in _nft_summarise_expr(expr)
    assert "accept" in _nft_summarise_expr(expr)


def test_summarise_set_right():
    expr = [
        {
            "match": {
                "op": "in",
                "left": {"ct": {"key": "state"}},
                "right": {"set": ["established", "related"]},
            }
        },
        {"accept": None},
    ]
    summary = _nft_summarise_expr(expr)
    assert "established" in summary
    assert "accept" in summary


def test_summarise_empty_expr():
    assert _nft_summarise_expr([]) == "(empty)"


# ---------------------------------------------------------------------------
# action plans — nft add/delete/flush
# ---------------------------------------------------------------------------

def test_plan_nft_add_rule_basic():
    plan = actions.plan_nft_add_rule("inet", "filter", "INPUT", "iifname eth0 accept")
    assert len(plan) == 1
    argv = plan[0]
    assert argv[:4] == ["nft", "add", "rule", "inet"]
    assert "filter" in argv
    assert "INPUT" in argv
    assert "iifname" in argv
    assert "eth0" in argv
    assert "accept" in argv


def test_plan_nft_add_rule_splits_expr():
    plan = actions.plan_nft_add_rule("ip", "nat", "POSTROUTING", "oifname eth0 masquerade")
    argv = plan[0]
    # Each token is a separate argv element — no shell injection via the expr string
    assert "oifname" in argv
    assert "eth0" in argv
    assert "masquerade" in argv


def test_plan_nft_delete_rule():
    plan = actions.plan_nft_delete_rule("inet", "filter", "INPUT", 42)
    assert len(plan) == 1
    argv = plan[0]
    assert argv == ["nft", "delete", "rule", "inet", "filter", "INPUT", "handle", "42"]


def test_plan_nft_flush_chain():
    plan = actions.plan_nft_flush_chain("inet", "filter", "INPUT")
    assert plan == [["nft", "flush", "chain", "inet", "filter", "INPUT"]]


def test_valid_nft_identifier_valid():
    assert actions.valid_nft_identifier("filter") is True
    assert actions.valid_nft_identifier("my_table") is True
    assert actions.valid_nft_identifier("INPUT") is True
    assert actions.valid_nft_identifier("chain-1") is True


def test_valid_nft_identifier_invalid():
    assert actions.valid_nft_identifier("") is False
    assert actions.valid_nft_identifier("1start") is False
    assert actions.valid_nft_identifier("has space") is False
    assert actions.valid_nft_identifier("a" * 65) is False


def test_nft_families_set():
    assert "ip" in actions.NFT_FAMILIES
    assert "inet" in actions.NFT_FAMILIES
    assert "ip6" in actions.NFT_FAMILIES


# ---------------------------------------------------------------------------
# demo_firewall
# ---------------------------------------------------------------------------

def test_demo_firewall_available():
    fw = demo_firewall()
    assert fw.available is True
    assert len(fw.tables) >= 2


def test_demo_firewall_eth0_has_rules():
    fw = demo_firewall()
    rules = fw.rules_for_iface("eth0")
    assert len(rules) >= 1


def test_demo_firewall_bond0_has_rules():
    fw = demo_firewall()
    rules = fw.rules_for_iface("bond0")
    assert len(rules) >= 1


def test_demo_firewall_chains_for_eth0():
    fw = demo_firewall()
    pairs = fw.chains_for_iface("eth0")
    assert len(pairs) >= 1
