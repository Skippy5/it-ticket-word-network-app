"""Interactive network HTML generator (vis-network).

Python owns all computation (preprocessing, co-occurrence, PMI, Louvain);
this module only renders the result and wires up client-side interaction:

- click a node  -> side panel lists every incident containing that term
- click an edge -> side panel lists incidents where BOTH terms co-occur
- hover a node  -> tooltip with term, frequency, community, top neighbors
- selection fades the rest of the graph (neighborhood highlight)
- physics toggle / re-stabilize buttons, deterministic layout seed
- every ticket_id is copyable and links out via a URL template

The vis-network library is inlined from assets/vis-network.min.js when
present (offline-friendly); otherwise a CDN <script> tag is emitted.
"""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

import config

_ASSETS = Path(__file__).parent / "assets"
_VIS_ASSET = _ASSETS / "vis-network.min.js"
_CSS_ASSET = _ASSETS / "network.css"
_JS_ASSET = _ASSETS / "network.js"
_CDN = "https://unpkg.com/vis-network@9.1.9/dist/vis-network.min.js"


def build_graph_payload(
    graph: nx.Graph,
    membership: dict[str, int],
    term_tickets: dict[str, list[str]],
    ticket_meta: dict[str, dict],
    cluster_info: list[dict],
    url_template: str = config.TICKET_URL_TEMPLATE,
    physics: bool = True,
    seed: int = 42,
    weighting: str = "count",
) -> dict:
    """Assemble the JSON payload consumed by the JS component.

    ticket_meta: ticket_id -> {short_description, priority, status, ...}
    """
    # Global ticket table; nodes/edges reference indices to keep payload small.
    all_ids = sorted({tid for ids in term_tickets.values() for tid in ids})
    tid_index = {tid: i for i, tid in enumerate(all_ids)}
    tickets = []
    for tid in all_ids:
        meta = ticket_meta.get(tid, {})
        tickets.append([
            tid,
            str(meta.get("short_description", ""))[:160],
            " · ".join(
                str(meta[k]) for k in ("priority", "status", "business_unit", "location")
                if meta.get(k)
            ),
        ])

    palette = config.PALETTE
    nodes = []
    for term in graph.nodes:
        cid = membership.get(term, 0)
        neighbors = sorted(
            graph[term].items(), key=lambda kv: -kv[1].get("weight", 0)
        )[:6]
        nodes.append({
            "id": term,
            "label": term.replace("_", " "),
            "value": graph.nodes[term]["freq"],
            "freq": graph.nodes[term]["freq"],
            "community": cid,
            "color": palette[cid % len(palette)],
            "neighbors": [
                f"{n.replace('_', ' ')} ({attrs.get('weight', 0):g})"
                for n, attrs in neighbors
            ],
            "tickets": [tid_index[t] for t in term_tickets.get(term, [])],
        })

    edges = []
    for a, b, attrs in graph.edges(data=True):
        shared = sorted(set(term_tickets.get(a, [])) & set(term_tickets.get(b, [])))
        edges.append({
            "id": f"{a}|||{b}",
            "from": a,
            "to": b,
            "value": attrs.get("weight", 1),
            "weight": attrs.get("weight", 1),
            "count": attrs.get("count", 0),
            "tickets": [tid_index[t] for t in shared],
        })

    legend = [
        {
            "id": c["community"],
            "color": palette[c["community"] % len(palette)],
            "size": c["size"],
            "top_terms": [t.replace("_", " ") for t in c["top_terms"][:6]],
        }
        for c in cluster_info
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "tickets": tickets,
        "legend": legend,
        "urlTemplate": url_template,
        "physics": bool(physics),
        "seed": int(seed),
        "weighting": weighting,
    }


def _vis_script_tag() -> str:
    if _VIS_ASSET.exists():
        return "<script>" + _VIS_ASSET.read_text(encoding="utf-8") + "</script>"
    return f'<script src="{_CDN}"></script>'


def generate_fragment(payload: dict, height: int = 680) -> str:
    """Self-contained HTML fragment for streamlit.components.v1.html / cli.py.

    Inlines the shared CSS + renderer (assets/network.css, assets/network.js)
    so the Streamlit component and the static web frontend render identically
    from one source of truth.
    """
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    css = _CSS_ASSET.read_text(encoding="utf-8")
    js = _JS_ASSET.read_text(encoding="utf-8")
    return _TEMPLATE.replace("__VIS_SCRIPT__", _vis_script_tag()) \
                    .replace("__CSS__", css) \
                    .replace("__NETWORK_JS__", js) \
                    .replace("__DATA__", data_json) \
                    .replace("__HEIGHT__", str(height))


def generate_standalone(payload: dict, title: str = "Ticket Word Network",
                        height: int = 760) -> str:
    """Full self-contained .html page (no server needed)."""
    body = generate_fragment(payload, height=height)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title></head><body style='margin:0;font-family:sans-serif'>"
        f"<h3 style='margin:10px 14px'>{title}</h3>{body}</body></html>"
    )


_TEMPLATE = r"""
__VIS_SCRIPT__
<style>__CSS__</style>
<div class="wn-root" style="height:__HEIGHT__px"></div>
<script>__NETWORK_JS__</script>
<script>
  (function () {
    const DATA = __DATA__;
    mountNetwork(document.querySelector(".wn-root"), DATA, { height: __HEIGHT__ });
  })();
</script>
"""
