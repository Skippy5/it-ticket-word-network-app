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

_ASSET = Path(__file__).parent / "assets" / "vis-network.min.js"
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
    if _ASSET.exists():
        return "<script>" + _ASSET.read_text(encoding="utf-8") + "</script>"
    return f'<script src="{_CDN}"></script>'


def generate_fragment(payload: dict, height: int = 680) -> str:
    """HTML fragment for streamlit.components.v1.html (also used by cli.py)."""
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return _TEMPLATE.replace("__VIS_SCRIPT__", _vis_script_tag()) \
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
<style>
  .wn-wrap { display:flex; gap:10px; height:__HEIGHT__px;
             font-family:"Segoe UI",system-ui,sans-serif; }
  #wn-graphbox { flex:1 1 auto; border:1px solid #d7dbe0; border-radius:8px;
                 background:#fafbfc; position:relative; min-width:0; }
  #wn-net  { position:absolute; inset:0; }
  #wn-side { flex:0 0 clamp(200px, 30%, 320px); border:1px solid #d7dbe0; border-radius:8px;
             padding:10px 12px; overflow-y:auto; background:#fff; font-size:13px; }
  .wn-toolbar { position:absolute; top:8px; left:8px; z-index:5; display:flex; gap:6px; }
  .wn-btn { font-size:12px; padding:3px 10px; border:1px solid #c4c9d0;
            border-radius:6px; background:#fff; cursor:pointer; }
  .wn-btn:hover { background:#eef1f4; }
  .wn-legend { position:absolute; bottom:8px; left:8px; z-index:5; background:rgba(255,255,255,.93);
               border:1px solid #d7dbe0; border-radius:8px; padding:6px 10px;
               font-size:11.5px; max-width:46%; max-height:38%; overflow-y:auto; }
  .wn-legend-row { display:flex; align-items:baseline; gap:6px; margin:2px 0; }
  .wn-dot { width:10px; height:10px; border-radius:50%; flex:0 0 auto; align-self:center; }
  .wn-side-title { font-weight:600; font-size:14px; margin-bottom:2px; }
  .wn-side-sub { color:#5a6472; margin-bottom:8px; }
  .wn-tk { border-top:1px solid #eceff2; padding:6px 0; }
  .wn-tk-id { font-family:Consolas,monospace; font-size:12.5px; font-weight:600; }
  .wn-tk-id a { color:#1a66cc; text-decoration:none; }
  .wn-tk-id a:hover { text-decoration:underline; }
  .wn-copy { font-size:10.5px; padding:1px 6px; margin-left:6px; border:1px solid #c4c9d0;
             border-radius:4px; background:#f6f8fa; cursor:pointer; }
  .wn-copy:hover { background:#e8ecf0; }
  .wn-tk-desc { color:#30363d; margin-top:1px; }
  .wn-tk-meta { color:#7a838f; font-size:11px; margin-top:1px; }
  .wn-hint { color:#7a838f; }
  .wn-pill { display:inline-block; padding:1px 8px; border-radius:10px;
             color:#fff; font-size:11px; margin-left:4px; }
  div.vis-tooltip { position:absolute; background:#fff; border:1px solid #c4c9d0;
                    border-radius:6px; padding:8px 10px; font-family:"Segoe UI",sans-serif;
                    font-size:12px; color:#222; box-shadow:0 2px 8px rgba(0,0,0,.12);
                    max-width:280px; white-space:normal; z-index:10; }
</style>

<div class="wn-wrap">
  <div id="wn-graphbox">
    <div id="wn-net"></div>
    <div class="wn-toolbar">
      <button class="wn-btn" id="wn-physics">Physics: ON</button>
      <button class="wn-btn" id="wn-fit">Fit</button>
      <button class="wn-btn" id="wn-clear">Clear selection</button>
    </div>
    <div class="wn-legend" id="wn-legend"></div>
  </div>
  <div id="wn-side">
    <div class="wn-side-title">Drill-in</div>
    <div class="wn-hint">Click a <b>node</b> to list every incident containing that term.<br><br>
    Click an <b>edge</b> to list the incidents where both terms co-occur &mdash;
    the evidence behind the connection.<br><br>
    Hover nodes for frequency, community and top neighbors.</div>
  </div>
</div>

<script>
const DATA = __DATA__;

function el(tag, attrs, text) {
  const e = document.createElement(tag);
  if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (text !== undefined) e.textContent = text;
  return e;
}

function nodeTooltip(n) {
  const box = el("div");
  box.appendChild(el("div", {style:"font-weight:600;font-size:13px"}, n.label));
  box.appendChild(el("div", null, "frequency: " + n.freq + " tickets"));
  box.appendChild(el("div", null, "community: " + n.community));
  if (n.neighbors.length) {
    box.appendChild(el("div", {style:"margin-top:4px;font-weight:600"}, "top neighbors"));
    n.neighbors.forEach(t => box.appendChild(el("div", null, "• " + t)));
  }
  return box;
}

function edgeTooltip(e) {
  const box = el("div");
  box.appendChild(el("div", {style:"font-weight:600"},
    e.from.replace(/_/g," ") + " ↔ " + e.to.replace(/_/g," ")));
  box.appendChild(el("div", null, "weight (" + DATA.weighting + "): " + e.weight));
  box.appendChild(el("div", null, "co-occurs in " + e.count + " tickets"));
  return box;
}

const nodeItems = DATA.nodes.map(n => ({
  id: n.id, label: n.label, value: n.value, title: nodeTooltip(n),
  color: { background: n.color, border: n.color,
           highlight: { background: n.color, border: "#222" } },
  font: { size: 16, color: "#1c2733", strokeWidth: 4, strokeColor: "#fafbfc" },
}));
const edgeItems = DATA.edges.map(e => ({
  id: e.id, from: e.from, to: e.to, value: e.value, title: edgeTooltip(e),
  color: { color: "#b8c1cc", highlight: "#445", opacity: 0.75 },
}));

const nodes = new vis.DataSet(nodeItems);
const edges = new vis.DataSet(edgeItems);
const nodeById = {}; DATA.nodes.forEach(n => nodeById[n.id] = n);
const edgeById = {}; DATA.edges.forEach(e => edgeById[e.id] = e);

const container = document.getElementById("wn-net");
const network = new vis.Network(container, { nodes, edges }, {
  layout: { randomSeed: DATA.seed, improvedLayout: true },
  nodes: { shape: "dot", scaling: { min: 8, max: 34,
           label: { enabled: true, min: 13, max: 24 } }, borderWidth: 1 },
  edges: { scaling: { min: 1, max: 9 }, smooth: { type: "continuous" },
           selectionWidth: 2 },
  physics: {
    enabled: DATA.physics,
    solver: "forceAtlas2Based",
    forceAtlas2Based: { gravitationalConstant: -55, springLength: 110,
                        springConstant: 0.06, damping: 0.5, avoidOverlap: 0.4 },
    stabilization: { iterations: 220, fit: true },
  },
  interaction: { hover: true, tooltipDelay: 120, multiselect: false },
});

/* ---------- legend ---------- */
const legend = document.getElementById("wn-legend");
legend.appendChild(el("div", {style:"font-weight:600;margin-bottom:2px"}, "Clusters"));
DATA.legend.forEach(c => {
  const row = el("div", {class:"wn-legend-row"});
  const dot = el("span", {class:"wn-dot"}); dot.style.background = c.color;
  row.appendChild(dot);
  row.appendChild(el("span", null,
    "C" + c.id + " (" + c.size + "): " + c.top_terms.join(", ")));
  legend.appendChild(row);
});

/* ---------- side panel ---------- */
const side = document.getElementById("wn-side");

function copyText(text, btn) {
  const done = () => { const o = btn.textContent; btn.textContent = "copied";
                       setTimeout(() => btn.textContent = o, 900); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  } else fallbackCopy(text, done);
}
function fallbackCopy(text, done) {
  const ta = el("textarea"); ta.value = text;
  document.body.appendChild(ta); ta.select();
  try { document.execCommand("copy"); } catch (e) {}
  document.body.removeChild(ta); done();
}

function renderTickets(indices, headerEl, subText) {
  side.innerHTML = "";
  side.appendChild(headerEl);
  const sub = el("div", {class:"wn-side-sub"}, subText);
  side.appendChild(sub);
  const allIds = indices.map(i => DATA.tickets[i][0]);
  const copyAll = el("button", {class:"wn-copy"}, "copy all IDs");
  copyAll.onclick = () => copyText(allIds.join(", "), copyAll);
  sub.appendChild(document.createTextNode(" "));
  sub.appendChild(copyAll);
  indices.forEach(i => {
    const [tid, desc, meta] = DATA.tickets[i];
    const row = el("div", {class:"wn-tk"});
    const idLine = el("div", {class:"wn-tk-id"});
    if (DATA.urlTemplate) {
      const a = el("a", {href: DATA.urlTemplate.replace("{ticket_id}", tid),
                         target: "_blank", rel: "noopener"}, tid);
      idLine.appendChild(a);
    } else idLine.appendChild(document.createTextNode(tid));
    const cp = el("button", {class:"wn-copy"}, "copy");
    cp.onclick = () => copyText(tid, cp);
    idLine.appendChild(cp);
    row.appendChild(idLine);
    if (desc) row.appendChild(el("div", {class:"wn-tk-desc"}, desc));
    if (meta) row.appendChild(el("div", {class:"wn-tk-meta"}, meta));
    side.appendChild(row);
  });
}

function showNode(id) {
  const n = nodeById[id];
  const head = el("div", {class:"wn-side-title"});
  head.appendChild(document.createTextNode(n.label));
  const pill = el("span", {class:"wn-pill"}, "C" + n.community);
  pill.style.background = n.color;
  head.appendChild(pill);
  renderTickets(n.tickets, head,
    n.tickets.length + " incident(s) contain this term");
}

function showEdge(id) {
  const e = edgeById[id];
  const head = el("div", {class:"wn-side-title"},
    e.from.replace(/_/g," ") + " ↔ " + e.to.replace(/_/g," "));
  renderTickets(e.tickets, head,
    e.tickets.length + " incident(s) where both terms co-occur · weight " + e.weight);
}

function resetPanel() {
  side.innerHTML = "";
  side.appendChild(el("div", {class:"wn-side-title"}, "Drill-in"));
  side.appendChild(el("div", {class:"wn-hint"},
    "Click a node or an edge to trace it back to incident numbers."));
}

/* ---------- neighborhood highlight ---------- */
const FADE = { background: "#e3e7eb", border: "#e3e7eb" };
function highlight(centerIds) {
  const keep = new Set(centerIds);
  centerIds.forEach(id => network.getConnectedNodes(id).forEach(n => keep.add(n)));
  nodes.update(DATA.nodes.map(n => keep.has(n.id)
    ? { id: n.id, color: { background: n.color, border: n.color },
        font: { color: "#1c2733" } }
    : { id: n.id, color: FADE, font: { color: "#b9c0c8" } }));
}
function unhighlight() {
  nodes.update(DATA.nodes.map(n =>
    ({ id: n.id, color: { background: n.color, border: n.color },
       font: { color: "#1c2733" } })));
}

network.on("click", params => {
  if (params.nodes.length) { showNode(params.nodes[0]); highlight([params.nodes[0]]); }
  else if (params.edges.length) {
    const e = edgeById[params.edges[0]];
    showEdge(params.edges[0]); highlight([e.from, e.to]);
  } else { resetPanel(); unhighlight(); }
});

/* ---------- toolbar ---------- */
let physicsOn = DATA.physics;
const pbtn = document.getElementById("wn-physics");
pbtn.textContent = "Physics: " + (physicsOn ? "ON" : "OFF");
pbtn.onclick = () => {
  physicsOn = !physicsOn;
  network.setOptions({ physics: { enabled: physicsOn } });
  pbtn.textContent = "Physics: " + (physicsOn ? "ON" : "OFF");
};
document.getElementById("wn-fit").onclick = () => network.fit({ animation: true });
document.getElementById("wn-clear").onclick = () => {
  network.unselectAll(); resetPanel(); unhighlight();
};
network.once("stabilizationIterationsDone", () => {
  if (!DATA.physics) network.setOptions({ physics: { enabled: false } });
});
</script>
"""
