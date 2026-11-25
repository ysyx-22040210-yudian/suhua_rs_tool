"""DUT hierarchy projection; all selectable values are verbatim NPI scope paths."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .npi_runner import HierarchyIndex, HierarchyScope


def _parent_path(scope: HierarchyScope, by_path: dict[str, HierarchyScope]) -> str:
    # The NPI leaf name, when available, disambiguates vendor spelling such
    # as a single escaped instance named "u.a". Never split its internal dot.
    path = scope.path
    if scope.name and path == scope.name:
        return ""
    suffix = "." + scope.name
    if scope.name and path.endswith(suffix):
        candidate = path[:-len(suffix)]
        if candidate in by_path:
            return candidate
        if candidate.rstrip() in by_path:
            return candidate.rstrip()
        path = candidate   # Unsupported intermediate scopes are not selectable.
    # Verilog escaped identifiers extend up to whitespace. Use only actual
    # indexed ancestors, not invented selectable nodes from string fragments.
    escaped = False
    separators = []
    for i, ch in enumerate(path):
        if escaped:
            if ch.isspace():
                escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ".":
            separators.append(i)
    for i in reversed(separators):
        candidate = path[:i]
        if candidate in by_path:
            return candidate
        if candidate.rstrip() in by_path:
            return candidate.rstrip()
    return ""


@dataclass
class ScopeTree:
    index: HierarchyIndex
    by_path: dict[str, HierarchyScope]
    parents: dict[str, str]
    children: dict[str, list[str]]

    @classmethod
    def from_index(cls, index: HierarchyIndex) -> ScopeTree:
        by_path = {s.path: s for s in index.scopes}
        parents = {}
        children: dict[str, list[str]] = {}
        # Collector paths are already unique and sorted. Linking them once
        # also handles multiple tops, generate indices and partial indices.
        for scope in index.scopes:
            parent = _parent_path(scope, by_path)
            parents[scope.path] = parent
            children.setdefault(parent, []).append(scope.path)
        return cls(index, by_path, parents, children)

    def filtered_children(self, matches: Iterable[str]) -> dict[str, list[str]]:
        """Retain the real branch for each hit, not every sibling of each hit."""
        nodes = set()
        for path in matches:
            while path and path not in nodes:
                nodes.add(path)
                path = self.parents[path]
        children: dict[str, list[str]] = {}
        for path in sorted(nodes):
            children.setdefault(self.parents[path], []).append(path)
        return children
