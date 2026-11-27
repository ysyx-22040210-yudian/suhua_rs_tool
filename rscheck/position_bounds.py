"""A forest of requested RTL boundaries, keyed by full hierarchy (not leaf prefixes)."""
from __future__ import annotations

from typing import Iterable, Iterator, Mapping

from .model import InventoryError, PositionInventory


SUBTREE_SCOPE = "partitioned_subtree_v1"


def enclosing_boundary(path: str, boundaries: set[str]) -> str | None:
    """Nearest *strict* ancestor. Dots inside Verilog escaped names aren't separators."""
    if "\\" not in path:
        while "." in path:
            path = path.rpartition(".")[0]
            if path in boundaries:
                return path
        return None
    nearest = None
    escaped = False
    for i, char in enumerate(path):
        if escaped:
            escaped = not char.isspace()
        elif char == "\\":
            escaped = True
        elif char == "." and path[:i].rstrip() in boundaries:
            nearest = path[:i].rstrip()
    return nearest


def boundary_forest(positions: Mapping[str, PositionInventory], scope: str | None,
                    active: Iterable[str]) -> tuple[dict[str, list[str]], Iterator[str]]:
    """Use the collector's actual scope edges when available, including for offline subsets."""
    nodes = set(positions) | set(active)
    children = {p: [] for p in nodes}
    parents = {}
    if scope == SUBTREE_SCOPE:
        for p, bucket in positions.items():
            for child in bucket.child_positions:
                if child not in positions or child == p or child in parents:
                    raise InventoryError(f"invalid or multiply-owned inventory child boundary: {p} -> {child}")
                parents[child] = p
                children[p].append(child)
    else:
        for child in nodes:
            parent = enclosing_boundary(child, nodes)
            if parent is not None:
                parents[child] = parent
                children[parent].append(child)
    # Iterative postorder, also guards malformed on-disk cycles.
    todo = sorted(nodes - parents.keys(), reverse=True)
    order = []
    while todo:
        parent = todo.pop()
        order.append(parent)
        children[parent].sort()
        todo.extend(reversed(children[parent]))
    if len(order) != len(nodes):
        raise InventoryError("cycle in inventory position boundaries")
    return children, reversed(order)
