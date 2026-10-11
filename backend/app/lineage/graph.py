"""Dataset-level lineage traversal. Pure functions; no database access."""

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class ImpactedNode:
    node_key: str
    depth: int
    path: tuple[str, ...]
    confidence: str


def downstream(
    edges: list[tuple[str, str]], origin: str, *, max_depth: int = 20
) -> list[ImpactedNode]:
    """Breadth-first walk from `origin` along declared edges.

    The graph is expected to be a DAG, but the walk tracks visited nodes so a bad edge
    can never loop forever. Each node is reported once, at its shortest path.
    """
    children: dict[str, list[str]] = {}
    for up, down in edges:
        children.setdefault(up, []).append(down)

    visited = {origin}
    queue: deque[tuple[str, tuple[str, ...]]] = deque([(origin, (origin,))])
    impacted: list[ImpactedNode] = []
    while queue:
        node, path = queue.popleft()
        depth = len(path) - 1
        if depth >= max_depth:
            continue
        for child in sorted(children.get(node, [])):
            if child in visited:
                continue
            visited.add(child)
            child_path = (*path, child)
            impacted.append(ImpactedNode(child, depth + 1, child_path, _confidence(depth + 1)))
            queue.append((child, child_path))
    return impacted


def _confidence(depth: int) -> str:
    # Declared edges say the data flows; they don't prove every downstream row changed.
    # Confidence decays with distance because filters and aggregations may absorb the issue.
    if depth <= 2:
        return "high"
    if depth <= 4:
        return "medium"
    return "low"


def has_cycle(edges: list[tuple[str, str]]) -> bool:
    graph: dict[str, list[str]] = {}
    indegree: dict[str, int] = {}
    for up, down in edges:
        graph.setdefault(up, []).append(down)
        indegree[down] = indegree.get(down, 0) + 1
        indegree.setdefault(up, 0)
    ready = deque(n for n, d in indegree.items() if d == 0)
    seen = 0
    while ready:
        node = ready.popleft()
        seen += 1
        for child in graph.get(node, []):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    return seen != len(indegree)
