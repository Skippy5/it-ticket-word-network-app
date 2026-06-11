"""Community detection: Louvain (networkx built-in), seeded for determinism."""

from __future__ import annotations

import networkx as nx


def louvain_communities(
    graph: nx.Graph,
    resolution: float = 1.0,
    seed: int = 42,
) -> dict[str, int]:
    """Return {term: community_id}. Communities are renumbered by descending
    size (community 0 is the largest) so colors are stable across runs."""
    if graph.number_of_nodes() == 0:
        return {}
    communities = nx.community.louvain_communities(
        graph, weight="weight", resolution=resolution, seed=seed
    )
    communities = sorted(communities, key=lambda c: (-len(c), sorted(c)[0]))
    membership: dict[str, int] = {}
    for cid, members in enumerate(communities):
        for term in members:
            membership[term] = cid
    return membership


def community_summary(
    membership: dict[str, int],
    term_freq: dict[str, int],
    top_n: int = 12,
) -> list[dict]:
    """Per-cluster term list ordered by frequency, for the legend/table."""
    clusters: dict[int, list[str]] = {}
    for term, cid in membership.items():
        clusters.setdefault(cid, []).append(term)
    out = []
    for cid in sorted(clusters):
        terms = sorted(clusters[cid], key=lambda t: (-term_freq.get(t, 0), t))
        out.append({
            "community": cid,
            "size": len(terms),
            "top_terms": terms[:top_n],
            "all_terms": terms,
        })
    return out
