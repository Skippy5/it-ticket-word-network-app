"""Stateless compute service shared by the API (and reusable elsewhere).

Wraps the existing engine — preprocess -> cooccurrence -> clustering ->
viz.build_graph_payload — behind three functions:

    normalize_dataframe(df)            : case-insensitive columns + parsed date
    filter_options(df, filters)        : cascading multi-select option lists
    apply_filters(df, filters)         : subset the population
    compute_network(df, filters, opts) : full network payload + stats + clusters

Nothing here holds state, so it drops straight into a serverless function or a
long-running container identically.
"""

from __future__ import annotations

import pandas as pd

import clustering
import config
import cooccurrence
import drilldown
import preprocess
import viz


# ---------------------------------------------------------------------------
# Dataframe normalization
# ---------------------------------------------------------------------------

def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    if config.ID_COLUMN in df.columns:
        df = df[df[config.ID_COLUMN].astype(str).str.strip() != ""].reset_index(drop=True)
    if config.DATE_COLUMN in df.columns:
        df["__opened_dt"] = pd.to_datetime(df[config.DATE_COLUMN], errors="coerce")
    return df


def available_text_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in config.TEXT_COLUMNS if c in df.columns]


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

# Order filters are applied / cascaded. business_unit then geo cascade then rest.
_PRIMARY = ["business_unit"] + config.GEO_CASCADE


def _filter_columns(df: pd.DataFrame) -> list[str]:
    ordered = _PRIMARY + [c for c in config.FILTER_COLUMNS if c not in _PRIMARY]
    return [c for c in ordered if c in df.columns]


def apply_filters(df: pd.DataFrame, filters: dict[str, list[str]]) -> pd.DataFrame:
    """Subset df by the given {column: [selected values]} filters."""
    out = df
    for col in _filter_columns(df):
        sel = filters.get(col)
        if sel:
            out = out[out[col].isin(sel)]
    # optional opened_at range as filters["__date"] = [iso_lo, iso_hi]
    rng = filters.get("__date")
    if rng and "__opened_dt" in out.columns and len(rng) == 2 and all(rng):
        lo, hi = pd.to_datetime(rng[0]), pd.to_datetime(rng[1])
        mask = out["__opened_dt"].between(lo, hi)
        out = out[mask | out["__opened_dt"].isna()]
    return out


def filter_options(df: pd.DataFrame, filters: dict[str, list[str]]) -> dict[str, list[str]]:
    """Cascading option lists: each column's options reflect the rows still in
    scope after applying *all other* selected filters (so country -> state ->
    location narrows naturally, and every facet stays consistent)."""
    options: dict[str, list[str]] = {}
    cols = _filter_columns(df)
    for col in cols:
        subset = df
        for other in cols:
            if other == col:
                continue
            sel = filters.get(other)
            if sel:
                subset = subset[subset[other].isin(sel)]
        vals = sorted({str(v) for v in subset[col].dropna().unique() if str(v).strip()})
        options[col] = vals
    return options


# ---------------------------------------------------------------------------
# Parameter coercion (tolerant of strings from JSON / form data)
# ---------------------------------------------------------------------------

def _coerce_stopwords(extra) -> list[str]:
    if not extra:
        return []
    if isinstance(extra, str):
        return [w for w in extra.split() if w.strip()]
    return [str(w).strip() for w in extra if str(w).strip()]


def _coerce_synonyms(syn) -> dict[str, str]:
    if not syn:
        return dict(config.SYNONYMS)
    if isinstance(syn, dict):
        return {str(k).strip().lower(): str(v).strip().lower()
                for k, v in syn.items() if str(k).strip() and str(v).strip()}
    out: dict[str, str] = {}
    for line in str(syn).splitlines():
        if "=>" in line:
            k, _, v = line.partition("=>")
            if k.strip() and v.strip():
                out[k.strip().lower()] = v.strip().lower()
    return out


def merge_params(opts: dict | None) -> dict:
    """Overlay user-supplied options onto config.DEFAULTS, with type coercion."""
    p = dict(config.DEFAULTS)
    opts = opts or {}
    for key in p:
        if key in opts and opts[key] is not None:
            p[key] = opts[key]
    # coerce numerics defensively
    for k in ("phrase_min_count", "window_size", "min_term_freq",
              "max_nodes", "max_edges_per_node", "seed"):
        p[k] = int(p[k])
    for k in ("phrase_threshold", "min_edge_weight", "min_edge_weight_pmi",
              "louvain_resolution"):
        p[k] = float(p[k])
    for k in ("phrase_detection", "physics"):
        p[k] = bool(p[k])
    return p


# ---------------------------------------------------------------------------
# Full network computation
# ---------------------------------------------------------------------------

def compute_network(
    df: pd.DataFrame,
    filters: dict | None = None,
    text_columns: list[str] | None = None,
    opts: dict | None = None,
    extra_stopwords=None,
    synonyms=None,
    url_template: str = config.TICKET_URL_TEMPLATE,
) -> dict:
    """Filter -> pipeline -> graph -> clusters -> render payload.

    Returns a JSON-serializable dict: {stats, clusters, payload, filter_options}.
    Never raises on empty results — returns an empty graph payload with a
    `message` instead.
    """
    filters = filters or {}
    p = merge_params(opts)
    total = len(df)

    text_columns = [c for c in (text_columns or config.DEFAULTS["text_columns"])
                    if c in df.columns] or available_text_columns(df)

    scoped = apply_filters(df, filters)
    options = filter_options(df, filters)

    base_stats = {
        "total": total,
        "in_scope": len(scoped),
        "n_nodes": 0,
        "n_edges": 0,
        "n_clusters": 0,
    }

    if scoped.empty or not text_columns:
        return {
            "stats": base_stats,
            "clusters": [],
            "payload": _empty_payload(url_template, p),
            "filter_options": options,
            "text_columns": text_columns,
            "message": "No tickets in scope for the current filters." if scoped.empty
                       else "No text columns selected.",
        }

    records = scoped.to_dict("records")
    stopword_set = preprocess.build_stopword_set(_coerce_stopwords(extra_stopwords))
    syn = _coerce_synonyms(synonyms)

    pipe = preprocess.run_pipeline(
        records, text_columns=text_columns,
        stopwords=stopword_set, synonyms=syn,
        phrase_detection=p["phrase_detection"],
        phrase_min_count=p["phrase_min_count"],
        phrase_threshold=p["phrase_threshold"],
    )
    result = cooccurrence.build_graph(
        pipe.docs, pipe.ticket_ids,
        weighting=p["weighting"], scope=p["cooc_scope"],
        window_size=p["window_size"], min_term_freq=p["min_term_freq"],
        min_edge_weight=p["min_edge_weight"], max_nodes=p["max_nodes"],
        max_edges_per_node=p["max_edges_per_node"],
    )
    graph = result.graph
    membership = clustering.louvain_communities(
        graph, resolution=p["louvain_resolution"], seed=p["seed"]
    )
    cluster_info = clustering.community_summary(membership, result.term_freq)

    meta_cols = [c for c in ("short_description", "priority", "status",
                             "business_unit", "location") if c in scoped.columns]
    ticket_meta = {
        str(r[config.ID_COLUMN]): {c: r.get(c, "") for c in meta_cols}
        for r in records
    }
    payload = viz.build_graph_payload(
        graph, membership, result.term_tickets, ticket_meta, cluster_info,
        url_template=url_template, physics=p["physics"],
        seed=p["seed"], weighting=p["weighting"],
    )

    clusters = [{
        "community": c["community"],
        "size": c["size"],
        "top_terms": [t.replace("_", " ") for t in c["top_terms"]],
    } for c in cluster_info]

    return {
        "stats": {
            **base_stats,
            "n_nodes": graph.number_of_nodes(),
            "n_edges": graph.number_of_edges(),
            "n_clusters": len(cluster_info),
        },
        "clusters": clusters,
        "payload": payload,
        "filter_options": options,
        "text_columns": text_columns,
        # export tables (so the browser can download without recomputing)
        "exports": {
            "nodes": drilldown.nodes_table(graph, membership, result.term_tickets)
                     .to_dict("records"),
            "edges": drilldown.edges_table(graph, result.term_tickets)
                     .to_dict("records"),
        },
        "message": "" if graph.number_of_nodes() else
                   "Empty network — lower min term frequency / min edge weight, "
                   "raise max nodes, or loosen the filters.",
    }


def _empty_payload(url_template: str, p: dict) -> dict:
    return {
        "nodes": [], "edges": [], "tickets": [], "legend": [],
        "urlTemplate": url_template, "physics": p["physics"],
        "seed": p["seed"], "weighting": p["weighting"],
    }
