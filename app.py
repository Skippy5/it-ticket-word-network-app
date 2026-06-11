"""IT Ticket Word Co-occurrence Network — Streamlit app.

Run:  streamlit run app.py
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import clustering
import config
import cooccurrence
import drilldown
import preprocess
import viz

st.set_page_config(page_title="Ticket Word Network", layout="wide", page_icon=":knot:")

DATA_DIR = Path(__file__).parent / "data"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def load_csv_bytes(raw: bytes, name: str) -> pd.DataFrame:
    """Read CSV bytes tolerantly (encoding fallbacks, bad lines skipped)."""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=enc, dtype=str,
                               on_bad_lines="skip", keep_default_na=False)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(io.BytesIO(raw), encoding="latin-1", dtype=str,
                       on_bad_lines="skip", keep_default_na=False)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Case-insensitive column matching: 'Ticket_ID ' -> 'ticket_id'."""
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df


def load_data(sources: list[tuple[str, bytes]]) -> pd.DataFrame | None:
    frames = []
    for name, raw in sources:
        try:
            df = normalize_columns(load_csv_bytes(raw, name))
        except Exception as exc:
            st.sidebar.error(f"Could not read **{name}**: {exc}")
            continue
        df["__source_file"] = name
        frames.append(df)
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    missing = [c for c in config.REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        st.error(
            f"Required column(s) missing: **{', '.join(missing)}**. "
            f"Found columns: {', '.join(df.columns)}"
        )
        return None
    # Drop rows without a ticket id; never invent IDs.
    df = df[df[config.ID_COLUMN].astype(str).str.strip() != ""].reset_index(drop=True)
    if config.DATE_COLUMN in df.columns:
        df["__opened_dt"] = pd.to_datetime(df[config.DATE_COLUMN], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# Sidebar — data source
# ---------------------------------------------------------------------------

st.sidebar.title("Ticket Word Network")

sample_files = sorted(DATA_DIR.glob("*.csv")) if DATA_DIR.exists() else []
source_mode = st.sidebar.radio(
    "Data source", ["Sample dataset", "Upload CSV"], horizontal=True
)

sources: list[tuple[str, bytes]] = []
if source_mode == "Sample dataset":
    if not sample_files:
        st.sidebar.warning("No sample CSVs found in ./data")
    chosen = st.sidebar.multiselect(
        "Sample file(s)",
        [f.name for f in sample_files],
        default=[sample_files[0].name] if sample_files else [],
    )
    for f in sample_files:
        if f.name in chosen:
            sources.append((f.name, f.read_bytes()))
else:
    uploads = st.sidebar.file_uploader(
        "Upload ticket CSV(s)", type=["csv"], accept_multiple_files=True
    )
    for up in uploads or []:
        sources.append((up.name, up.getvalue()))

df = load_data(sources) if sources else None
if df is None or df.empty:
    st.title("IT Ticket Word Co-occurrence Network")
    st.info("Pick a sample dataset or upload one or more ticket CSVs in the sidebar to begin.")
    st.stop()

total_tickets = len(df)

# Warn (don't fail) about absent optional columns.
absent_text = [c for c in config.TEXT_COLUMNS if c not in df.columns]
if absent_text:
    st.sidebar.warning(f"Text column(s) not in this file: {', '.join(absent_text)}")

# ---------------------------------------------------------------------------
# Sidebar — text columns + filters
# ---------------------------------------------------------------------------

available_text = [c for c in config.TEXT_COLUMNS if c in df.columns]
text_columns = st.sidebar.multiselect(
    "Text columns (1 ticket = 1 document)",
    available_text,
    default=[c for c in config.DEFAULTS["text_columns"] if c in available_text],
)

st.sidebar.subheader("Filters")
if st.sidebar.button("Reset filters", use_container_width=True):
    for key in list(st.session_state.keys()):
        if key.startswith("flt_"):
            del st.session_state[key]
    st.rerun()

filtered = df.copy()

# Primary filters: business unit, then the geographic cascade
# country -> state -> location (options narrow as you pick).
if "business_unit" in filtered.columns:
    bu_options = sorted(v for v in df["business_unit"].dropna().unique()
                        if str(v).strip())
    bu_sel = st.sidebar.multiselect("business unit", bu_options, key="flt_business_unit")
    if bu_sel:
        filtered = filtered[filtered["business_unit"].isin(bu_sel)]

for col in config.GEO_CASCADE:
    if col not in filtered.columns:
        continue
    options = sorted(v for v in filtered[col].dropna().unique() if str(v).strip())
    sel = st.sidebar.multiselect(col.replace("_", " "), options, key=f"flt_{col}")
    if sel:
        filtered = filtered[filtered[col].isin(sel)]

# Remaining categorical filters (options from the full dataset).
other_filters = [c for c in config.FILTER_COLUMNS
                 if c not in config.GEO_CASCADE and c != "business_unit"
                 and c in df.columns]
with st.sidebar.expander("More filters", expanded=False):
    for col in other_filters:
        options = sorted(v for v in df[col].dropna().unique() if str(v).strip())
        sel = st.multiselect(col.replace("_", " "), options, key=f"flt_{col}")
        if sel:
            filtered = filtered[filtered[col].isin(sel)]

    if "__opened_dt" in df.columns and df["__opened_dt"].notna().any():
        dmin = df["__opened_dt"].min().date()
        dmax = df["__opened_dt"].max().date()
        rng = st.date_input("opened_at range", value=(dmin, dmax),
                            min_value=dmin, max_value=dmax, key="flt_daterange")
        if isinstance(rng, tuple) and len(rng) == 2:
            lo, hi = rng
            if (lo, hi) != (dmin, dmax):
                mask = filtered["__opened_dt"].dt.date.between(lo, hi)
                filtered = filtered[mask | filtered["__opened_dt"].isna()]

# ---------------------------------------------------------------------------
# Sidebar — network parameters
# ---------------------------------------------------------------------------

with st.sidebar.expander("Network parameters", expanded=False):
    D = config.DEFAULTS
    phrase_detection = st.toggle("Phrase detection (bigrams)", value=D["phrase_detection"])
    cooc_scope = st.selectbox("Co-occurrence scope", ["document", "window"],
                              index=0 if D["cooc_scope"] == "document" else 1)
    window_size = st.slider("Window size (tokens)", 3, 25, D["window_size"],
                            disabled=(cooc_scope != "window"))
    weighting = st.selectbox("Edge weighting", ["count", "pmi"],
                             index=0 if D["weighting"] == "count" else 1,
                             help="pmi = positive PMI; surfaces meaningful "
                                  "associations instead of just frequent pairs")
    min_term_freq = st.slider("Min term frequency (tickets)", 1, 20, D["min_term_freq"])
    default_edge_w = D["min_edge_weight"] if weighting == "count" else D["min_edge_weight_pmi"]
    min_edge_weight = st.number_input("Min edge weight", min_value=0.0,
                                      value=float(default_edge_w), step=0.1,
                                      key=f"edge_w_{weighting}")
    max_nodes = st.slider("Max nodes (top-N terms)", 10, 250, D["max_nodes"])
    max_edges_per_node = st.slider(
        "Max edges per node (0 = all)", 0, 30, D["max_edges_per_node"],
        help="Keeps each node's strongest K edges — prunes the hairball "
             "while preserving cluster structure")
    resolution = st.slider("Louvain resolution", 0.4, 2.5, D["louvain_resolution"], 0.1)
    physics = st.toggle("Physics on load", value=D["physics"])

with st.sidebar.expander("Stop words / synonyms", expanded=False):
    st.caption("Standard English stop words are always applied. "
               "Below is the editable IT/ticketing extension (one per line).")
    stopword_text = st.text_area(
        "Extra stop words", value="\n".join(config.IT_STOPWORDS), height=160
    )
    st.caption("Synonyms / abbreviations, one per line, `from => to`. "
               "Applied before counting; multi-word targets feed phrase detection.")
    synonym_text = st.text_area(
        "Synonym map",
        value="\n".join(f"{k} => {v}" for k, v in config.SYNONYMS.items()),
        height=160,
    )
    url_template = st.text_input("Ticket URL template", value=config.TICKET_URL_TEMPLATE,
                                 help="{ticket_id} is replaced in drill-in links")

extra_stopwords = [w for w in stopword_text.split() if w.strip()]
synonyms: dict[str, str] = {}
for line in synonym_text.splitlines():
    if "=>" in line:
        k, _, v = line.partition("=>")
        if k.strip() and v.strip():
            synonyms[k.strip().lower()] = v.strip().lower()

# ---------------------------------------------------------------------------
# Main — pipeline
# ---------------------------------------------------------------------------

st.title("IT Ticket Word Co-occurrence Network")
st.caption(f"Showing **{len(filtered)}** of **{total_tickets}** incidents in scope "
           f"— the network below is computed on exactly this population.")

if filtered.empty:
    st.warning("No tickets match the current filters. Loosen or reset the filters "
               "in the sidebar to see a network.")
    st.stop()
if not text_columns:
    st.warning("Select at least one text column in the sidebar.")
    st.stop()

records = filtered.to_dict("records")
stopword_set = preprocess.build_stopword_set(extra_stopwords)

with st.spinner("Processing text and building network..."):
    pipe = preprocess.run_pipeline(
        records,
        text_columns=text_columns,
        stopwords=stopword_set,
        synonyms=synonyms,
        phrase_detection=phrase_detection,
        phrase_min_count=config.DEFAULTS["phrase_min_count"],
        phrase_threshold=config.DEFAULTS["phrase_threshold"],
    )
    result = cooccurrence.build_graph(
        pipe.docs,
        pipe.ticket_ids,
        weighting=weighting,
        scope=cooc_scope,
        window_size=window_size,
        min_term_freq=min_term_freq,
        min_edge_weight=min_edge_weight,
        max_nodes=max_nodes,
        max_edges_per_node=max_edges_per_node,
    )
    graph = result.graph
    membership = clustering.louvain_communities(
        graph, resolution=resolution, seed=config.DEFAULTS["seed"]
    )
    cluster_info = clustering.community_summary(membership, result.term_freq)

if graph.number_of_nodes() == 0:
    st.warning(
        "The current population/parameters produced an empty network. "
        "Try lowering **min term frequency** or **min edge weight**, raising "
        "**max nodes**, or loosening the filters."
    )
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Incidents in scope", len(filtered))
c2.metric("Terms (nodes)", graph.number_of_nodes())
c3.metric("Connections (edges)", graph.number_of_edges())
c4.metric("Clusters", len(cluster_info))

# ---------------------------------------------------------------------------
# Main — network component
# ---------------------------------------------------------------------------

meta_cols = [c for c in ("short_description", "priority", "status",
                         "business_unit", "location") if c in filtered.columns]
ticket_meta = {
    str(rec[config.ID_COLUMN]): {c: rec.get(c, "") for c in meta_cols}
    for rec in records
}

payload = viz.build_graph_payload(
    graph, membership, result.term_tickets, ticket_meta, cluster_info,
    url_template=url_template, physics=physics,
    seed=config.DEFAULTS["seed"], weighting=weighting,
)
components.html(viz.generate_fragment(payload, height=680), height=700)

# ---------------------------------------------------------------------------
# Main — cluster table + exports
# ---------------------------------------------------------------------------

tab_clusters, tab_export = st.tabs(["Clusters", "Export"])

with tab_clusters:
    rows = [{
        "Cluster": f"C{c['community']}",
        "Terms": c["size"],
        "Top terms": ", ".join(t.replace("_", " ") for t in c["top_terms"]),
    } for c in cluster_info]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

with tab_export:
    nodes_df = drilldown.nodes_table(graph, membership, result.term_tickets)
    edges_df = drilldown.edges_table(graph, result.term_tickets)
    incidents_df = filtered.drop(
        columns=[c for c in ("__opened_dt",) if c in filtered.columns]
    )
    graph_json = json.dumps({
        "nodes": payload["nodes"], "edges": payload["edges"],
        "tickets": payload["tickets"],
    }, indent=2)

    e1, e2, e3, e4 = st.columns(4)
    e1.download_button("Nodes CSV", nodes_df.to_csv(index=False).encode("utf-8"),
                       "network_nodes.csv", "text/csv", use_container_width=True)
    e2.download_button("Edges CSV", edges_df.to_csv(index=False).encode("utf-8"),
                       "network_edges.csv", "text/csv", use_container_width=True)
    e3.download_button("Filtered incidents CSV",
                       incidents_df.to_csv(index=False).encode("utf-8"),
                       "incidents_in_scope.csv", "text/csv", use_container_width=True)
    e4.download_button("Graph JSON", graph_json.encode("utf-8"),
                       "network_graph.json", "application/json", use_container_width=True)
    st.caption("Edges CSV includes the co-occurring incident IDs per connection; "
               "nodes CSV includes every incident ID per term — full traceability.")
