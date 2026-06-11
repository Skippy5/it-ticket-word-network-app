"""CLI: build a standalone interactive network.html from a CSV (no server).

Example:
    python cli.py data/it_tickets_large.csv --out network.html
    python cli.py data/*.csv --weighting pmi --max-nodes 80 --out network.html
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

import clustering
import config
import cooccurrence
import preprocess
import viz


def main(argv: list[str] | None = None) -> int:
    D = config.DEFAULTS
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", nargs="+", help="ticket CSV file(s)")
    ap.add_argument("--columns", default=",".join(D["text_columns"]),
                    help="comma-separated text columns")
    ap.add_argument("--weighting", choices=["count", "pmi"], default=D["weighting"])
    ap.add_argument("--scope", choices=["document", "window"], default=D["cooc_scope"])
    ap.add_argument("--window", type=int, default=D["window_size"])
    ap.add_argument("--min-term-freq", type=int, default=D["min_term_freq"])
    ap.add_argument("--min-edge-weight", type=float, default=None)
    ap.add_argument("--max-nodes", type=int, default=D["max_nodes"])
    ap.add_argument("--max-edges-per-node", type=int,
                    default=D["max_edges_per_node"])
    ap.add_argument("--resolution", type=float, default=D["louvain_resolution"])
    ap.add_argument("--no-phrases", action="store_true")
    ap.add_argument("--out", default="network.html")
    args = ap.parse_args(argv)

    min_edge = args.min_edge_weight
    if min_edge is None:
        min_edge = D["min_edge_weight"] if args.weighting == "count" else D["min_edge_weight_pmi"]

    frames = []
    for path in args.csv:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, on_bad_lines="skip")
        df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    if config.ID_COLUMN not in df.columns:
        print(f"ERROR: required column '{config.ID_COLUMN}' not found "
              f"(have: {', '.join(df.columns)})", file=sys.stderr)
        return 2
    df = df[df[config.ID_COLUMN].astype(str).str.strip() != ""]

    text_columns = [c.strip() for c in args.columns.split(",")
                    if c.strip() in df.columns]
    if not text_columns:
        print("ERROR: none of the requested text columns exist in the CSV",
              file=sys.stderr)
        return 2

    records = df.to_dict("records")
    pipe = preprocess.run_pipeline(
        records, text_columns=text_columns,
        phrase_detection=not args.no_phrases,
        phrase_min_count=D["phrase_min_count"],
        phrase_threshold=D["phrase_threshold"],
    )
    result = cooccurrence.build_graph(
        pipe.docs, pipe.ticket_ids,
        weighting=args.weighting, scope=args.scope, window_size=args.window,
        min_term_freq=args.min_term_freq, min_edge_weight=min_edge,
        max_nodes=args.max_nodes, max_edges_per_node=args.max_edges_per_node,
    )
    membership = clustering.louvain_communities(
        result.graph, resolution=args.resolution, seed=D["seed"]
    )
    cluster_info = clustering.community_summary(membership, result.term_freq)

    meta_cols = [c for c in ("short_description", "priority", "status",
                             "business_unit", "location") if c in df.columns]
    ticket_meta = {
        str(r[config.ID_COLUMN]): {c: r.get(c, "") for c in meta_cols}
        for r in records
    }
    payload = viz.build_graph_payload(
        result.graph, membership, result.term_tickets, ticket_meta, cluster_info,
        physics=True, seed=D["seed"], weighting=args.weighting,
    )
    html = viz.generate_standalone(
        payload, title=f"Ticket Word Network — {len(df)} incidents"
    )
    Path(args.out).write_text(html, encoding="utf-8")

    print(f"tickets:  {len(df)}")
    print(f"nodes:    {result.graph.number_of_nodes()}")
    print(f"edges:    {result.graph.number_of_edges()}")
    print(f"clusters: {len(cluster_info)}")
    for c in cluster_info:
        print(f"  C{c['community']} ({c['size']}): "
              + ", ".join(t.replace("_", " ") for t in c["top_terms"][:8]))
    print(f"wrote:    {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
