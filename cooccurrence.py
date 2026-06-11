"""Co-occurrence matrix + graph construction.

Vectorized as required: a binary documents x terms matrix, then ``X.T @ X``
yields the full term-term co-occurrence matrix in one shot. Optional
sliding-window scope for longer notes. Edge weights are raw co-document
counts or positive PMI.

numpy-only (no scipy): the vocabulary is pruned to the top ``max_nodes``
terms (<= a few hundred) BEFORE the matrix is built, so the dense
docs x terms matrix stays tiny — e.g. 10,000 tickets x 250 terms is 2.5 MB
of uint8 — and the matmul is instant. Dropping scipy/scikit-learn keeps the
serverless (Vercel) deployment bundle far under the platform size limit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import networkx as nx


@dataclass
class GraphResult:
    graph: nx.Graph                  # nodes: term, attrs freq; edges: weight, count
    term_freq: dict[str, int]        # document frequency per kept term
    term_tickets: dict[str, list[str]]  # term -> ticket_ids containing it (sorted)
    n_docs: int


def _binary_doc_term_matrix(docs: list[list[str]], vocab: dict[str, int]) -> np.ndarray:
    """Binary (0/1) docs x terms matrix as a small dense numpy array."""
    X = np.zeros((len(docs), len(vocab)), dtype=np.int32)
    for i, tokens in enumerate(docs):
        for t in set(tokens):
            j = vocab.get(t)
            if j is not None:
                X[i, j] = 1
    return X


def _window_pair_counts(
    docs: list[list[str]], vocab: dict[str, int], window: int
) -> np.ndarray:
    """Sliding-window co-occurrence: pair counted once per document if the two
    terms appear within `window` tokens of each other."""
    n = len(vocab)
    C = np.zeros((n, n), dtype=np.int32)
    for tokens in docs:
        idx = [(pos, vocab[t]) for pos, t in enumerate(tokens) if t in vocab]
        pairs = set()
        for a in range(len(idx)):
            pos_a, term_a = idx[a]
            for b in range(a + 1, len(idx)):
                pos_b, term_b = idx[b]
                if pos_b - pos_a > window:
                    break
                if term_a != term_b:
                    pairs.add((min(term_a, term_b), max(term_a, term_b)))
        for i, j in pairs:
            C[i, j] += 1
    return C + C.T


def build_graph(
    docs: list[list[str]],
    ticket_ids: list[str],
    weighting: str = "count",          # "count" | "pmi"
    scope: str = "document",           # "document" | "window"
    window_size: int = 8,
    min_term_freq: int = 3,
    min_edge_weight: float = 2.0,
    max_nodes: int = 140,
    max_edges_per_node: int = 14,
) -> GraphResult:
    """Build the pruned term co-occurrence graph.

    Pruning order: min term document-frequency -> top max_nodes by frequency
    -> co-occurrence -> min edge weight -> per-node top-K backbone
    (an edge survives if it is among either endpoint's strongest
    max_edges_per_node edges; 0 disables). Every kept term keeps its full
    list of ticket_ids (traceability is never pruned).
    """
    n_docs = len(docs)

    # --- document frequency + vocabulary pruning -------------------------
    df: dict[str, int] = {}
    for tokens in docs:
        for t in set(tokens):
            df[t] = df.get(t, 0) + 1

    kept = [t for t, c in df.items() if c >= min_term_freq]
    kept.sort(key=lambda t: (-df[t], t))          # deterministic order
    kept = kept[:max_nodes]
    vocab = {t: i for i, t in enumerate(kept)}

    if not kept or n_docs == 0:
        return GraphResult(nx.Graph(), {}, {}, n_docs)

    # --- term -> ticket ids (drill-in source of truth) --------------------
    term_tickets: dict[str, list[str]] = {t: [] for t in kept}
    for tid, tokens in zip(ticket_ids, docs):
        for t in set(tokens):
            if t in vocab:
                term_tickets[t].append(tid)

    # --- co-occurrence matrix (X.T @ X, vectorized) -----------------------
    X = _binary_doc_term_matrix(docs, vocab)
    if scope == "window":
        C = _window_pair_counts(docs, vocab, window_size)
    else:
        C = X.T @ X
    np.fill_diagonal(C, 0)

    doc_freq = X.sum(axis=0).astype(np.float64)

    # --- edge weights ------------------------------------------------------
    graph = nx.Graph()
    for term in kept:
        graph.add_node(term, freq=int(df[term]))

    upper_i, upper_j = np.nonzero(np.triu(C, k=1))
    for i, j in zip(upper_i.tolist(), upper_j.tolist()):
        count = float(C[i, j])
        if weighting == "pmi":
            # positive PMI over document co-occurrence probabilities
            p_ij = count / n_docs
            p_i = doc_freq[i] / n_docs
            p_j = doc_freq[j] / n_docs
            pmi = np.log2(p_ij / (p_i * p_j)) if p_i > 0 and p_j > 0 else 0.0
            weight = max(pmi, 0.0)
        else:
            weight = count
        if weight >= min_edge_weight and count >= 1:
            graph.add_edge(
                kept[i], kept[j],
                weight=round(float(weight), 4),
                count=int(count),
            )

    # Per-node top-K backbone: dense templated text otherwise produces a
    # hairball. An edge is kept when either endpoint ranks it in its top K.
    if max_edges_per_node and max_edges_per_node > 0:
        keep: set[tuple[str, str]] = set()
        for node in graph.nodes:
            strongest = sorted(
                graph[node].items(), key=lambda kv: -kv[1]["weight"]
            )[:max_edges_per_node]
            for nbr, _ in strongest:
                keep.add((node, nbr) if node <= nbr else (nbr, node))
        drop = [
            (a, b) for a, b in graph.edges
            if ((a, b) if a <= b else (b, a)) not in keep
        ]
        graph.remove_edges_from(drop)

    # Nodes with no surviving edges are removed from the *picture* only;
    # term_tickets retains everything for export.
    isolated = [n for n in graph.nodes if graph.degree(n) == 0]
    graph.remove_nodes_from(isolated)

    freq = {t: int(df[t]) for t in graph.nodes}
    tickets = {t: sorted(term_tickets[t]) for t in graph.nodes}
    return GraphResult(graph=graph, term_freq=freq, term_tickets=tickets, n_docs=n_docs)
