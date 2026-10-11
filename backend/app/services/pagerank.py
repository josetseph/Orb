"""Personalized PageRank over the knowledge graph (HippoRAG 2's retrieval walk)."""

from __future__ import annotations

from collections import defaultdict


def personalized_pagerank(
    edges: list[tuple[str, str]], seeds: dict[str, float], damping: float = 0.5, iterations: int = 30
) -> dict[str, float]:
    """Stationary visit probabilities of a walk that, at each step, follows an edge with probability ``damping``
    and jumps back to a seed (chosen by seed weight) otherwise. Edges are undirected. Nodes not reachable from
    a seed score 0 and are left out. A node with no edges sends its mass back to the seeds.
    """
    total = sum(w for w in seeds.values() if w > 0)
    if total <= 0:
        return {}
    restart = {n: w / total for n, w in seeds.items() if w > 0}
    neighbours: dict[str, list[str]] = defaultdict(list)
    for a, b in edges:
        if a != b:
            neighbours[a].append(b)
            neighbours[b].append(a)
    score = dict(restart)
    for _ in range(iterations):
        nxt: dict[str, float] = defaultdict(float)
        for node, mass in score.items():
            out = neighbours.get(node)
            if out:
                share = damping * mass / len(out)
                for m in out:
                    nxt[m] += share
            else:  # a dead end: its walk restarts
                for s, r in restart.items():
                    nxt[s] += damping * mass * r
        for s, r in restart.items():
            nxt[s] += (1 - damping) * r
        score = nxt
    return dict(score)
