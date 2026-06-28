"""Parse nftables ruleset from ``nft -j list ruleset`` output.

This module is pure Python, Qt-free, and read-only — it builds
:class:`~netgrip.core.model.NftRuleset` objects from the kernel's JSON output
without executing anything.

Parse path::

    nft -j list ruleset  →  parse_ruleset()  →  NftRuleset
        flat list of "table" / "chain" / "rule" dicts
        assembled into NftTable → NftChain → NftRule hierarchy

The expression renderer (``_render_exprs``) converts the nested JSON expression
tree to a compact, human-readable string.  It covers the common stmts: verdicts,
match (meta / payload / ct), counter, log, limit, NAT, reject.  Unknown nodes
fall back to their JSON repr so nothing is silently dropped.
"""

from __future__ import annotations

import json
from typing import Any

from netgrip.core.model import NftChain, NftRule, NftRuleset, NftTable

# Read the full nftables ruleset as JSON.  Requires nft ≥ 0.9.1 (Linux 5.0+);
# fails with a non-zero exit on hosts without nftables or nft — callers should
# treat any RuntimeError as "no firewall data available".
NFT_COMMAND = ["nft", "-j", "list", "ruleset"]


def parse_ruleset(raw: str | dict[str, Any]) -> NftRuleset:
    """Parse ``nft -j list ruleset`` output into an :class:`NftRuleset`.

    Args:
        raw: The raw JSON string returned by the command, or an already-decoded
             dict (handy for tests that pass a fixture directly).

    Raises:
        ValueError: If the JSON is invalid or not a recognised nftables schema.
    """
    if isinstance(raw, str):
        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"nft output is not valid JSON: {exc}") from exc
    else:
        data = raw

    if not isinstance(data, dict) or "nftables" not in data:
        raise ValueError("Expected top-level {\"nftables\": [...]} structure")

    items: list[Any] = data["nftables"]

    # --- pass 1: build table and chain objects (no rules yet) ---
    # Keyed (family, table_name) → NftTable
    tables: dict[tuple[str, str], NftTable] = {}
    # Keyed (family, table_name, chain_name) → NftChain
    chains: dict[tuple[str, str, str], NftChain] = {}

    for item in items:
        if not isinstance(item, dict):
            continue

        if "table" in item:
            t = item["table"]
            fam = t.get("family", "")
            name = t.get("name", "")
            handle = t.get("handle", 0)
            tkey: tuple[str, str] = (fam, name)
            if tkey not in tables:
                tables[tkey] = NftTable(name=name, family=fam, handle=handle)

        elif "chain" in item:
            c = item["chain"]
            fam = c.get("family", "")
            tname = c.get("table", "")
            cname = c.get("name", "")
            handle = c.get("handle", 0)
            chain = NftChain(
                name=cname,
                table=tname,
                family=fam,
                handle=handle,
                type=c.get("type", ""),
                hook=c.get("hook", ""),
                prio=int(c.get("prio", 0)),
                policy=c.get("policy", ""),
            )
            chains[(fam, tname, cname)] = chain
            # Attach to its parent table (create a stub if the table entry was omitted)
            tkey = (fam, tname)
            if tkey not in tables:
                tables[tkey] = NftTable(name=tname, family=fam)
            tables[tkey].chains.append(chain)

    # --- pass 2: parse rules and append to their chains ---
    for item in items:
        if not isinstance(item, dict) or "rule" not in item:
            continue
        r = item["rule"]
        fam = r.get("family", "")
        tname = r.get("table", "")
        cname = r.get("chain", "")
        handle = int(r.get("handle", 0))
        expr_list: list[Any] = r.get("expr", [])
        comment = r.get("comment", "")

        rule = NftRule(
            handle=handle,
            chain=cname,
            table=tname,
            family=fam,
            expr_str=_render_exprs(expr_list),
            comment=comment,
            ifaces=_extract_ifaces(expr_list),
        )

        chain = chains.get((fam, tname, cname))
        if chain is not None:
            chain.rules.append(rule)

    return NftRuleset(tables=list(tables.values()))


# ---------------------------------------------------------------------------
# Expression rendering
# ---------------------------------------------------------------------------

def _render_exprs(expr_list: list[Any]) -> str:
    """Render a rule's expression list to a compact, human-readable string."""
    parts: list[str] = []
    for expr in expr_list:
        s = _render_expr(expr)
        if s:
            parts.append(s)
    return " ".join(parts)


def _render_expr(expr: Any) -> str:  # noqa: C901
    """Render a single nft expression object to a string."""
    if expr is None:
        return ""
    if isinstance(expr, (str, int, float)):
        return str(expr)
    if isinstance(expr, list):
        return "{ " + ", ".join(_render_expr(e) for e in expr) + " }"
    if not isinstance(expr, dict):
        return str(expr)

    # Verdicts -----------------------------------------------------------------
    for v in ("accept", "drop", "return", "continue"):
        if v in expr:
            return v
    if "goto" in expr:
        g = expr["goto"]
        return f"goto {g.get('target', '?') if isinstance(g, dict) else g}"
    if "jump" in expr:
        j = expr["jump"]
        return f"jump {j.get('target', '?') if isinstance(j, dict) else j}"

    # Match --------------------------------------------------------------------
    if "match" in expr:
        m = expr["match"]
        op = m.get("op", "==")
        left = _render_val(m.get("left", {}))
        right = _render_val(m.get("right"))
        if op in ("in", "!= in"):
            return f"{left} {op} {{ {right} }}"
        return f"{left} {op} {right}"

    # Counter ------------------------------------------------------------------
    if "counter" in expr:
        c = expr["counter"]
        if isinstance(c, dict):
            pkts = c.get("packets", 0)
            bts = c.get("bytes", 0)
            return f"counter packets {pkts} bytes {bts}"
        return "counter"

    # Log ----------------------------------------------------------------------
    if "log" in expr:
        log_obj = expr["log"]
        if isinstance(log_obj, dict):
            prefix = log_obj.get("prefix")
            if prefix:
                return f"log prefix {prefix!r}"
        return "log"

    # Limit --------------------------------------------------------------------
    if "limit" in expr:
        lim = expr["limit"]
        if isinstance(lim, dict):
            rate = lim.get("rate", "?")
            per = lim.get("per", "second")
            burst = lim.get("burst")
            s = f"limit rate {rate}/{per}"
            if burst:
                s += f" burst {burst} packets"
            return s
        return "limit"

    # NAT / masquerade / redirect ----------------------------------------------
    for nat_kw in ("snat", "dnat"):
        if nat_kw in expr:
            val = expr[nat_kw]
            if isinstance(val, dict):
                addr = val.get("addr")
                if addr is not None:
                    return f"{nat_kw} to {_render_val(addr)}"
            return nat_kw
    if "masquerade" in expr:
        return "masquerade"
    if "redirect" in expr:
        val = expr["redirect"]
        if isinstance(val, dict) and "port" in val:
            return f"redirect to {val['port']}"
        return "redirect"

    # Conntrack ----------------------------------------------------------------
    if "ct" in expr:
        ct = expr["ct"]
        if isinstance(ct, dict):
            return f"ct {ct.get('key', '?')}"
        return "ct"

    # Queue --------------------------------------------------------------------
    if "queue" in expr:
        q = expr["queue"]
        if isinstance(q, dict):
            return f"queue to {q.get('num', '?')}"
        return "queue"

    # Reject -------------------------------------------------------------------
    if "reject" in expr:
        rej = expr["reject"]
        if isinstance(rej, dict):
            return f"reject with {rej.get('expr', 'icmp type port-unreachable')}"
        return "reject"

    # Mangle -------------------------------------------------------------------
    if "mangle" in expr:
        m = expr["mangle"]
        if isinstance(m, dict):
            key = _render_val(m.get("key", {}))
            val = _render_val(m.get("value"))
            return f"mangle {key} set {val}"
        return "mangle"

    # Fallback: JSON repr so nothing is silently swallowed
    return str(expr)


def _render_val(val: Any) -> str:
    """Render a match value (left or right side of a match expression)."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return str(val).lower()
    if isinstance(val, (str, int, float)):
        return str(val)
    if isinstance(val, list):
        return "{ " + ", ".join(_render_val(v) for v in val) + " }"
    if not isinstance(val, dict):
        return str(val)

    # meta key (iifname, oifname, l4proto, …)
    if "meta" in val:
        return val["meta"].get("key", "meta")
    # payload field
    if "payload" in val:
        p = val["payload"]
        return f"{p.get('protocol', '?')} {p.get('field', '?')}"
    # conntrack
    if "ct" in val:
        ct = val["ct"]
        if isinstance(ct, dict):
            return f"ct {ct.get('key', '?')}"
        return "ct"
    # CIDR prefix
    if "prefix" in val:
        p = val["prefix"]
        return f"{p.get('addr', '?')}/{p.get('len', '?')}"
    # Set literal
    if "set" in val:
        return "{ " + ", ".join(_render_val(v) for v in val["set"]) + " }"
    # Range (e.g. port range 1024-65535)
    if "range" in val:
        r = val["range"]
        if isinstance(r, list) and len(r) == 2:
            return f"{_render_val(r[0])}-{_render_val(r[1])}"
    # Named set / map element reference
    if "elem" in val:
        inner = val["elem"]
        if isinstance(inner, dict) and "ref" in inner:
            return f"@{inner['ref'].get('name', '?')}"
        return _render_val(inner)
    if "map" in val:
        m = val["map"]
        if isinstance(m, dict):
            return f"map({_render_val(m.get('key', {}))}, {_render_val(m.get('data', {}))})"
        return "map"
    # Nested expression (some nft constructs wrap a value in "expr")
    if "expr" in val:
        return _render_expr(val["expr"])
    return str(val)


# ---------------------------------------------------------------------------
# Interface name extraction
# ---------------------------------------------------------------------------

_IFACE_META_KEYS: frozenset[str] = frozenset(("iifname", "oifname", "iif", "oif"))


def _extract_ifaces(expr_list: list[Any]) -> list[str]:
    """Return interface names explicitly matched via iifname/oifname in a rule.

    Only concrete string names are returned; wildcard patterns and set
    references are omitted — the goal is accurate UI grouping, not an exhaustive
    dependency graph.
    """
    ifaces: list[str] = []
    for expr in expr_list:
        if not isinstance(expr, dict) or "match" not in expr:
            continue
        m = expr["match"]
        left = m.get("left", {})
        right = m.get("right")
        if not isinstance(left, dict) or "meta" not in left:
            continue
        if left["meta"].get("key") not in _IFACE_META_KEYS:
            continue
        # Right side: a plain string name, or a set of strings
        if isinstance(right, str) and right:
            ifaces.append(right)
        elif isinstance(right, dict) and "set" in right:
            ifaces.extend(item for item in right["set"] if isinstance(item, str) and item)
    return ifaces
