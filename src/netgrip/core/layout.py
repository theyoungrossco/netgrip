"""Topology-aware layered layout for the canvas.

Pure Python (no Qt): given a graph of interface / IP-group boxes, their
measured sizes, and which boxes are natural sources (physical NICs), assign
each box an ``(x, y)`` so that

  * related boxes form left-to-right chains — a parent and its child, a veth
    and its peer, a NIC and the bridge it's enslaved to sit in adjacent
    columns, so a deep Proxmox path
    (``eno → vmbr0 → fwpr ↔ fwln → fwbr → tap``) reads straight across;
  * boxes in the same column never overlap;
  * connector lines cross as little as possible (median/barycenter ordering).

The canvas measures the boxes (that needs Qt) and applies the positions; all
the graph reasoning lives here so it stays headless-testable, per the project's
"``core`` never imports Qt" rule.

The algorithm is a small layered ("Sugiyama-style") graph drawing:

1. **Layering** — multi-source breadth-first search from the source boxes
   assigns each box a column = its hop distance from the nearest source.
2. **Ordering** — a few median sweeps reorder boxes within each column to pull
   neighbours into line and cut crossings.
3. **Coordinates** — columns get adaptive widths (boxes centred within them so
   centre-to-centre edges run near-horizontal); vertical positions are nudged
   toward each box's neighbours and then de-overlapped with an order-preserving
   pool-adjacent-violators pass.

Disconnected pieces (a standalone NIC, a lone veth pair, loopback) are laid out
independently and stacked top to bottom.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    """A node to place: a stable ``key`` and its measured size."""

    key: str
    width: float
    height: float


def solve(
    boxes: list[Box],
    edges: list[tuple[str, str]],
    sources: list[str],
    priority: list[str] | None = None,
    *,
    margin_x: float = 30.0,
    margin_y: float = 30.0,
    col_gap: float = 40.0,
    row_gap: float = 22.0,
    comp_gap: float = 44.0,
    sweeps: int = 4,
) -> dict[str, tuple[float, float]]:
    """Place ``boxes`` and return ``{key: (x, y)}`` (top-left of each box).

    ``edges`` are undirected ``(a, b)`` pairs; unknown keys are ignored.
    ``sources`` seed column 0 (physical NICs). ``priority`` is a global box
    order used to break ties deterministically and to order both columns and
    disconnected components; boxes missing from it sort last.
    """
    by_key = {b.key: b for b in boxes}
    if not by_key:
        return {}

    prio = {k: i for i, k in enumerate(priority or [])}
    rank = len(prio)

    def prio_index(key: str) -> tuple[int, str]:
        # Fall back to the key itself so the order is always total & stable.
        return (prio.get(key, rank), key)

    adj: dict[str, set[str]] = {k: set() for k in by_key}
    for a, b in edges:
        if a in adj and b in adj and a != b:
            adj[a].add(b)
            adj[b].add(a)

    source_set = {s for s in sources if s in by_key}

    positions: dict[str, tuple[float, float]] = {}
    # Lay each connected piece out on its own, then stack them vertically in
    # priority order (physical-rooted pieces first, loopback last).
    components = _components(by_key, adj, prio_index)
    y_cursor = margin_y
    for comp in components:
        local, height = _layout_component(
            comp, adj, by_key, source_set, prio_index,
            col_gap=col_gap, row_gap=row_gap, sweeps=sweeps,
        )
        for key, (x, y) in local.items():
            positions[key] = (margin_x + x, y_cursor + y)
        y_cursor += height + comp_gap
    return positions


def _components(
    by_key: dict[str, Box],
    adj: dict[str, set[str]],
    prio_index,
) -> list[list[str]]:
    """Connected components, each as a key list, ordered by their most
    preferred member so the canvas stacks them sensibly."""
    seen: set[str] = set()
    comps: list[list[str]] = []
    for start in sorted(by_key, key=prio_index):
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        members: list[str] = []
        while stack:
            u = stack.pop()
            members.append(u)
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        comps.append(members)
    comps.sort(key=lambda m: min(prio_index(k) for k in m))
    return comps


def _layout_component(
    comp: list[str],
    adj: dict[str, set[str]],
    by_key: dict[str, Box],
    source_set: set[str],
    prio_index,
    *,
    col_gap: float,
    row_gap: float,
    sweeps: int,
) -> tuple[dict[str, tuple[float, float]], float]:
    """Layout one connected component in local coords (origin at 0, 0).

    Returns ``({key: (x, y)}, total_height)``.
    """
    layer = _assign_layers(comp, adj, source_set, prio_index)
    max_layer = max(layer.values())

    # Boxes per column, seeded in breadth-first / priority order, then reordered
    # by repeated median sweeps to reduce crossings.
    order: dict[int, list[str]] = defaultdict(list)
    for key in sorted(comp, key=lambda k: (layer[k], prio_index(k))):
        order[layer[key]].append(key)
    _order_columns(order, max_layer, layer, adj, sweeps)

    node_x = _assign_x(order, max_layer, by_key, col_gap)
    node_y = _assign_y(order, max_layer, by_key, adj, layer, row_gap, sweeps)

    if comp:
        top = min(node_y[k] for k in comp)
        for k in comp:
            node_y[k] -= top
        height = max(node_y[k] + by_key[k].height for k in comp)
    else:
        height = 0.0
    return {k: (node_x[k], node_y[k]) for k in comp}, height


def _assign_layers(
    comp: list[str],
    adj: dict[str, set[str]],
    source_set: set[str],
    prio_index,
) -> dict[str, int]:
    """Column per box = hop distance from the nearest source. All sources in
    the component start at column 0 (multi-source BFS); a source-less component
    starts from its most preferred box."""
    seeds = [k for k in sorted(comp, key=prio_index) if k in source_set]
    if not seeds:
        seeds = [min(comp, key=prio_index)]

    layer: dict[str, int] = {}
    queue: deque[str] = deque()
    for s in seeds:
        layer[s] = 0
        queue.append(s)
    while queue:
        u = queue.popleft()
        for v in sorted(adj[u], key=prio_index):
            if v not in layer:
                layer[v] = layer[u] + 1
                queue.append(v)
    # Any box unreachable from the seeds (shouldn't happen for a connected
    # component, but stay safe) goes to column 0.
    for k in comp:
        layer.setdefault(k, 0)
    return layer


def _order_columns(
    order: dict[int, list[str]],
    max_layer: int,
    layer: dict[str, int],
    adj: dict[str, set[str]],
    sweeps: int,
) -> None:
    """Reorder boxes within each column toward the median position of their
    neighbours in the adjacent column — the classic crossing-reduction sweep.
    Column 0 is kept in its seeded (priority) order as a stable anchor."""

    def sweep(col: int, ref: int) -> None:
        ref_pos = {k: i for i, k in enumerate(order[ref])}
        keyed: list[tuple[float, int, str]] = []
        for pos, key in enumerate(order[col]):
            neighbours = sorted(ref_pos[n] for n in adj[key] if layer.get(n) == ref)
            if neighbours:
                mid = len(neighbours) // 2
                median = (
                    neighbours[mid]
                    if len(neighbours) % 2
                    else (neighbours[mid - 1] + neighbours[mid]) / 2
                )
            else:
                # No anchor this side: keep current spot (stable).
                median = float(pos)
            keyed.append((median, pos, key))
        keyed.sort()
        order[col] = [k for _, _, k in keyed]

    for _ in range(sweeps):
        for col in range(1, max_layer + 1):  # downward: anchor to the left
            sweep(col, col - 1)
        for col in range(max_layer - 1, 0, -1):  # upward: anchor to the right
            sweep(col, col + 1)


def _assign_x(
    order: dict[int, list[str]],
    max_layer: int,
    by_key: dict[str, Box],
    col_gap: float,
) -> dict[str, float]:
    """Adaptive column widths; each box centred within its column so
    centre-to-centre edges between columns run as horizontal as possible."""
    col_width = {
        col: max((by_key[k].width for k in order[col]), default=0.0)
        for col in range(max_layer + 1)
    }
    col_left: dict[int, float] = {}
    x = 0.0
    for col in range(max_layer + 1):
        col_left[col] = x
        x += col_width[col] + col_gap

    node_x: dict[str, float] = {}
    for col in range(max_layer + 1):
        for key in order[col]:
            node_x[key] = col_left[col] + (col_width[col] - by_key[key].width) / 2
    return node_x


def _assign_y(
    order: dict[int, list[str]],
    max_layer: int,
    by_key: dict[str, Box],
    adj: dict[str, set[str]],
    layer: dict[str, int],
    row_gap: float,
    sweeps: int,
) -> dict[str, float]:
    """Vertical coordinates: start packed by column order, then repeatedly pull
    each box toward its neighbours' average centre and de-overlap, alternating
    which side anchors so parents centre over children and vice-versa."""
    node_y: dict[str, float] = {}
    for col in range(max_layer + 1):
        cur = 0.0
        for key in order[col]:
            node_y[key] = cur
            cur += by_key[key].height + row_gap

    def center(key: str) -> float:
        return node_y[key] + by_key[key].height / 2

    def place(col: int, ref: int) -> None:
        keys = order[col]
        desired_top: list[float] = []
        for key in keys:
            centres = [center(n) for n in adj[key] if layer.get(n) == ref]
            target = sum(centres) / len(centres) if centres else center(key)
            desired_top.append(target - by_key[key].height / 2)
        heights = [by_key[k].height for k in keys]
        for key, top in zip(keys, _pav(desired_top, heights, row_gap), strict=True):
            node_y[key] = top

    for _ in range(sweeps):
        for col in range(max_layer + 1):  # downward: average left neighbours
            place(col, col - 1)
        for col in range(max_layer, -1, -1):  # upward: average right neighbours
            place(col, col + 1)
    return node_y


def _pav(desired_top: list[float], heights: list[float], gap: float) -> list[float]:
    """Order-preserving placement: tops closest (least-squares) to ``desired_top``
    such that consecutive boxes keep at least ``gap`` between them.

    Removing the fixed ``height + gap`` staircase turns the spacing constraint
    into "non-decreasing", solved optimally by pool-adjacent-violators.
    """
    n = len(desired_top)
    if n == 0:
        return []
    prefix = [0.0] * n
    for i in range(1, n):
        prefix[i] = prefix[i - 1] + heights[i - 1] + gap
    shifted = [desired_top[i] - prefix[i] for i in range(n)]

    values: list[float] = []
    counts: list[int] = []
    for x in shifted:
        v, c = x, 1
        while values and values[-1] > v:
            v = (values[-1] * counts[-1] + v * c) / (counts[-1] + c)
            c += counts.pop()
            values.pop()
        values.append(v)
        counts.append(c)

    flat: list[float] = []
    for v, c in zip(values, counts, strict=True):
        flat.extend([v] * c)
    return [flat[i] + prefix[i] for i in range(n)]
