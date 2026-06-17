"""The topology-aware canvas layout (pure, Qt-free: see core/layout.py).

These exercise the legibility guarantees the layout exists to provide:
parent->child and veth chains run left-to-right, sibling chains don't cross,
and boxes in a column never overlap.
"""

from collections import defaultdict

from netgrip.core import layout

W, H = 160.0, 60.0
ROW_GAP = 22.0


def _boxes(keys, w=W, h=H):
    return [layout.Box(k, w, h) for k in keys]


def _columns(pos):
    """Group keys by their (rounded) x — i.e. by layout column."""
    cols = defaultdict(list)
    for key, (x, _y) in pos.items():
        cols[round(x, 3)].append(key)
    return cols


def _center_y(pos, key, h=H):
    return pos[key][1] + h / 2


def test_parent_child_chain_runs_left_to_right():
    # The Proxmox shape: a VM's path is four hops past the bridge.
    chain = ["eno1", "vmbr0", "fwpr", "fwln", "fwbr", "tap"]
    edges = [
        ("eno1", "vmbr0"),
        ("vmbr0", "fwpr"),
        ("fwpr", "fwln"),  # the veth pair
        ("fwln", "fwbr"),
        ("fwbr", "tap"),
    ]
    pos = layout.solve(_boxes(chain), edges, sources=["eno1"])

    xs = [pos[k][0] for k in chain]
    assert xs == sorted(xs)
    assert len(set(xs)) == len(chain)  # one column per hop, strictly rightward


def test_veth_peers_land_adjacent_and_level():
    # A bare veth pair (container case): no physical source at all.
    pos = layout.solve(
        _boxes(["veth-host", "veth-ns"]),
        [("veth-host", "veth-ns")],
        sources=[],
    )
    # Adjacent columns...
    assert pos["veth-host"][0] != pos["veth-ns"][0]
    # ...and level with each other, so their cable is a clean horizontal line.
    assert abs(_center_y(pos, "veth-host") - _center_y(pos, "veth-ns")) < 1.0


def test_parallel_vm_chains_do_not_cross():
    # One bridge feeding two identical per-NIC firewall chains. If the within-
    # column ordering is right, chain A stays on the same side of chain B in
    # every column, which means none of their cables cross.
    keys = ["eno1", "vmbr0"]
    edges = [("eno1", "vmbr0")]
    for vm in ("a", "b"):
        fwpr, fwln, fwbr, tap = (f"fwpr-{vm}", f"fwln-{vm}", f"fwbr-{vm}", f"tap-{vm}")
        keys += [fwpr, fwln, fwbr, tap]
        edges += [
            ("vmbr0", fwpr), (fwpr, fwln), (fwln, fwbr), (fwbr, tap),
        ]
    pos = layout.solve(_boxes(keys), edges, sources=["eno1"])

    cols = ["fwpr", "fwln", "fwbr", "tap"]
    signs = {
        col: _center_y(pos, f"{col}-a") - _center_y(pos, f"{col}-b") for col in cols
    }
    # Every column orders A vs B the same way -> no crossing cables.
    assert all(d < 0 for d in signs.values()) or all(d > 0 for d in signs.values())


def test_no_overlap_within_a_column():
    # A fan-out of differently-sized boxes onto one source; the column must
    # stack them with the row gap and never overlap.
    boxes = [
        layout.Box("s0", W, 60),
        layout.Box("c1", W, 40),
        layout.Box("c2", W, 120),
        layout.Box("c3", W, 30),
        layout.Box("c4", W, 80),
    ]
    heights = {b.key: b.height for b in boxes}
    edges = [("s0", k) for k in ("c1", "c2", "c3", "c4")]
    pos = layout.solve(boxes, edges, sources=["s0"], row_gap=ROW_GAP)

    column = sorted(("c1", "c2", "c3", "c4"), key=lambda k: pos[k][1])
    for upper, lower in zip(column, column[1:], strict=False):  # intentional pairwise
        assert pos[lower][1] >= pos[upper][1] + heights[upper] + ROW_GAP - 1e-6


def test_disconnected_pieces_stack_with_loopback_last():
    boxes = _boxes(["eth0", "veth-a", "veth-b", "lo"])
    edges = [("veth-a", "veth-b")]  # eth0 and lo are islands
    priority = ["eth0", "veth-a", "veth-b", "lo"]  # loopback intentionally last
    pos = layout.solve(boxes, edges, sources=["eth0"], priority=priority)

    # Each piece occupies its own vertical band, stacked in priority order.
    assert pos["eth0"][1] < pos["veth-a"][1] < pos["lo"][1]
    # The veth island keeps its peers level even without a physical source.
    assert abs(_center_y(pos, "veth-a") - _center_y(pos, "veth-b")) < 1.0


def test_layout_is_deterministic():
    keys = ["eno1", "vmbr0", "fwpr", "fwln", "fwbr", "tap"]
    edges = [
        ("eno1", "vmbr0"), ("vmbr0", "fwpr"), ("fwpr", "fwln"),
        ("fwln", "fwbr"), ("fwbr", "tap"),
    ]
    first = layout.solve(_boxes(keys), edges, sources=["eno1"])
    second = layout.solve(_boxes(keys), edges, sources=["eno1"])
    assert first == second


def test_empty_input_returns_empty():
    assert layout.solve([], [], sources=[]) == {}


def test_unknown_edges_are_ignored():
    # An edge to a box that isn't in the set must not raise.
    pos = layout.solve(_boxes(["a"]), [("a", "ghost")], sources=["a"])
    assert set(pos) == {"a"}
