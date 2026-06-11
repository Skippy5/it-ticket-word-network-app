"""Drill-in lookups: term -> incidents, edge -> incidents, and export tables.

The whole point of the app is traceability: every node and edge resolves back
to the exact ticket_ids that produced it.
"""

from __future__ import annotations

import networkx as nx
import pandas as pd


def term_incidents(term: str, term_tickets: dict[str, list[str]]) -> list[str]:
    """All ticket_ids whose document contains `term`."""
    return list(term_tickets.get(term, []))


def edge_incidents(
    term_a: str, term_b: str, term_tickets: dict[str, list[str]]
) -> list[str]:
    """Ticket_ids where BOTH terms appear — the evidence behind an edge."""
    a = set(term_tickets.get(term_a, []))
    b = set(term_tickets.get(term_b, []))
    return sorted(a & b)


def nodes_table(
    graph: nx.Graph,
    membership: dict[str, int],
    term_tickets: dict[str, list[str]],
) -> pd.DataFrame:
    rows = []
    for term in sorted(graph.nodes, key=lambda t: (-graph.nodes[t]["freq"], t)):
        tickets = term_tickets.get(term, [])
        rows.append({
            "term": term,
            "frequency": graph.nodes[term]["freq"],
            "community": membership.get(term, -1),
            "degree": graph.degree(term),
            "ticket_ids": ";".join(tickets),
        })
    return pd.DataFrame(rows)


def edges_table(
    graph: nx.Graph,
    term_tickets: dict[str, list[str]],
) -> pd.DataFrame:
    rows = []
    for a, b, attrs in sorted(
        graph.edges(data=True), key=lambda e: -e[2].get("weight", 0)
    ):
        shared = edge_incidents(a, b, term_tickets)
        rows.append({
            "source": a,
            "target": b,
            "weight": attrs.get("weight", 0),
            "cooccurrence_count": attrs.get("count", 0),
            "ticket_ids": ";".join(shared),
        })
    return pd.DataFrame(rows)
