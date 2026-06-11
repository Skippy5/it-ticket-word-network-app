/* Shared interactive renderer for the IT-ticket co-occurrence network.
 *
 * Single source of truth for the vis-network rendering + drill-in behaviour,
 * used by BOTH the Streamlit component (viz.py inlines this file) and the
 * static web frontend (web/index.html loads it via <script>). Requires
 * vis-network (global `vis`) to be loaded first, plus network.css.
 *
 *   mountNetwork(rootEl, DATA, { height })
 *
 * DATA shape (produced by viz.build_graph_payload):
 *   { nodes:[{id,label,value,freq,community,color,neighbors,tickets:[idx]}],
 *     edges:[{id,from,to,value,weight,count,tickets:[idx]}],
 *     tickets:[[id, short_description, meta]],
 *     legend:[{id,color,size,top_terms}],
 *     urlTemplate, physics, seed, weighting }
 *
 * Everything is scoped to `rootEl` (queried via class, not global id) so any
 * number of networks can live on the same host page / portal.
 */
(function (global) {
  "use strict";

  function el(tag, attrs, text) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  /* Canvas-drawn colors come from the same --wn-* variables network.css uses,
     so the graph follows the host page's light/dark theme. Re-read on every
     call: the host toggles <html data-theme> then calls refreshTheme(). */
  function themeColors() {
    return {
      label: cssVar("--wn-node-label", "#1c2733"),
      labelStroke: cssVar("--wn-canvas", "#fafbfc"),
      edge: cssVar("--wn-edge", "#b8c1cc"),
      edgeHighlight: cssVar("--wn-edge-highlight", "#444455"),
      fade: cssVar("--wn-fade", "#e3e7eb"),
      fadeLabel: cssVar("--wn-fade-label", "#b9c0c8"),
    };
  }

  const WRAP_HTML =
    '<div class="wn-wrap">' +
    '  <div class="wn-graphbox">' +
    '    <div class="wn-net"></div>' +
    '    <div class="wn-toolbar">' +
    '      <button class="wn-btn wn-physics">Physics: ON</button>' +
    '      <button class="wn-btn wn-fit">Fit</button>' +
    '      <button class="wn-btn wn-clear">Clear selection</button>' +
    '    </div>' +
    '    <div class="wn-legend"></div>' +
    '  </div>' +
    '  <div class="wn-side"></div>' +
    "</div>";

  function mountNetwork(root, DATA, opts) {
    opts = opts || {};
    if (typeof global.vis === "undefined") {
      root.innerHTML =
        '<div style="padding:16px;color:#a33;font-family:sans-serif">' +
        "vis-network failed to load.</div>";
      return null;
    }
    if (opts.height) root.style.height =
      typeof opts.height === "number" ? opts.height + "px" : opts.height;

    root.innerHTML = WRAP_HTML;
    const netEl = root.querySelector(".wn-net");
    const sideEl = root.querySelector(".wn-side");
    const legendEl = root.querySelector(".wn-legend");

    function nodeTooltip(n) {
      const box = el("div");
      box.appendChild(el("div", { style: "font-weight:600;font-size:13px" }, n.label));
      box.appendChild(el("div", null, "frequency: " + n.freq + " tickets"));
      box.appendChild(el("div", null, "community: " + n.community));
      if (n.neighbors && n.neighbors.length) {
        box.appendChild(el("div", { style: "margin-top:4px;font-weight:600" }, "top neighbors"));
        n.neighbors.forEach((t) => box.appendChild(el("div", null, "• " + t)));
      }
      return box;
    }

    function edgeTooltip(e) {
      const box = el("div");
      box.appendChild(el("div", { style: "font-weight:600" },
        e.from.replace(/_/g, " ") + " ↔ " + e.to.replace(/_/g, " ")));
      box.appendChild(el("div", null, "weight (" + DATA.weighting + "): " + e.weight));
      box.appendChild(el("div", null, "co-occurs in " + e.count + " tickets"));
      return box;
    }

    let TH = themeColors();
    const nodeItems = DATA.nodes.map((n) => ({
      id: n.id, label: n.label, value: n.value, title: nodeTooltip(n),
      color: { background: n.color, border: n.color,
               highlight: { background: n.color, border: "#222" } },
      font: { size: 16, color: TH.label, strokeWidth: 4, strokeColor: TH.labelStroke },
    }));
    const edgeItems = DATA.edges.map((e) => ({
      id: e.id, from: e.from, to: e.to, value: e.value, title: edgeTooltip(e),
      color: { color: TH.edge, highlight: TH.edgeHighlight, opacity: 0.75 },
    }));

    const nodes = new global.vis.DataSet(nodeItems);
    const edges = new global.vis.DataSet(edgeItems);
    const nodeById = {}; DATA.nodes.forEach((n) => (nodeById[n.id] = n));
    const edgeById = {}; DATA.edges.forEach((e) => (edgeById[e.id] = e));

    const network = new global.vis.Network(netEl, { nodes, edges }, {
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

    /* ---------- legend (optional on-map cluster key) ----------
       Hidden when opts.showLegend === false (e.g. the web build, which shows a
       dedicated Clusters panel below the map). Defaults to on so the
       self-contained HTML export keeps an on-map cluster key. */
    if (opts.showLegend === false) {
      legendEl.remove();
    } else {
      legendEl.appendChild(el("div", { style: "font-weight:600;margin-bottom:2px" }, "Clusters"));
      DATA.legend.forEach((c) => {
        const row = el("div", { class: "wn-legend-row" });
        const dot = el("span", { class: "wn-dot" }); dot.style.background = c.color;
        row.appendChild(dot);
        row.appendChild(el("span", null,
          "C" + c.id + " (" + c.size + "): " + c.top_terms.join(", ")));
        legendEl.appendChild(row);
      });
    }

    /* ---------- side panel / drill-in ---------- */
    function copyText(text, btn) {
      const done = () => { const o = btn.textContent; btn.textContent = "copied";
                           setTimeout(() => (btn.textContent = o), 900); };
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
      sideEl.innerHTML = "";
      sideEl.appendChild(headerEl);
      const sub = el("div", { class: "wn-side-sub" }, subText);
      sideEl.appendChild(sub);
      const allIds = indices.map((i) => DATA.tickets[i][0]);
      const copyAll = el("button", { class: "wn-copy" }, "copy all IDs");
      copyAll.onclick = () => copyText(allIds.join(", "), copyAll);
      sub.appendChild(document.createTextNode(" "));
      sub.appendChild(copyAll);
      indices.forEach((i) => {
        const row3 = DATA.tickets[i];
        const tid = row3[0], desc = row3[1], meta = row3[2];
        const row = el("div", { class: "wn-tk" });
        const idLine = el("div", { class: "wn-tk-id" });
        if (DATA.urlTemplate) {
          const a = el("a", { href: DATA.urlTemplate.replace("{ticket_id}", tid),
                              target: "_blank", rel: "noopener" }, tid);
          idLine.appendChild(a);
        } else idLine.appendChild(document.createTextNode(tid));
        const cp = el("button", { class: "wn-copy" }, "copy");
        cp.onclick = () => copyText(tid, cp);
        idLine.appendChild(cp);
        row.appendChild(idLine);
        if (desc) row.appendChild(el("div", { class: "wn-tk-desc" }, desc));
        if (meta) row.appendChild(el("div", { class: "wn-tk-meta" }, meta));
        sideEl.appendChild(row);
      });
    }

    function showNode(id) {
      const n = nodeById[id];
      const head = el("div", { class: "wn-side-title" });
      head.appendChild(document.createTextNode(n.label));
      const pill = el("span", { class: "wn-pill" }, "C" + n.community);
      pill.style.background = n.color;
      head.appendChild(pill);
      renderTickets(n.tickets, head, n.tickets.length + " incident(s) contain this term");
    }

    function showEdge(id) {
      const e = edgeById[id];
      const head = el("div", { class: "wn-side-title" },
        e.from.replace(/_/g, " ") + " ↔ " + e.to.replace(/_/g, " "));
      renderTickets(e.tickets, head,
        e.tickets.length + " incident(s) where both terms co-occur · weight " + e.weight);
    }

    function resetPanel() {
      sideEl.innerHTML = "";
      sideEl.appendChild(el("div", { class: "wn-side-title" }, "Drill-in"));
      sideEl.appendChild(el("div", { class: "wn-hint" },
        "Click a node or an edge to trace it back to incident numbers."));
    }
    resetPanel();

    /* ---------- neighborhood highlight ---------- */
    function highlight(centerIds) {
      const keep = new Set(centerIds);
      const fade = { background: TH.fade, border: TH.fade };
      centerIds.forEach((id) => network.getConnectedNodes(id).forEach((n) => keep.add(n)));
      nodes.update(DATA.nodes.map((n) => keep.has(n.id)
        ? { id: n.id, color: { background: n.color, border: n.color }, font: { color: TH.label } }
        : { id: n.id, color: fade, font: { color: TH.fadeLabel } }));
    }
    function unhighlight() {
      nodes.update(DATA.nodes.map((n) =>
        ({ id: n.id, color: { background: n.color, border: n.color }, font: { color: TH.label } })));
    }

    /* Re-read the --wn-* variables (after the host toggled data-theme) and
       restyle everything that is drawn on the canvas. */
    function refreshTheme() {
      TH = themeColors();
      unhighlight();
      nodes.update(DATA.nodes.map((n) =>
        ({ id: n.id, font: { color: TH.label, strokeColor: TH.labelStroke } })));
      edges.update(DATA.edges.map((e) =>
        ({ id: e.id, color: { color: TH.edge, highlight: TH.edgeHighlight, opacity: 0.75 } })));
    }

    network.on("click", (params) => {
      if (params.nodes.length) { showNode(params.nodes[0]); highlight([params.nodes[0]]); }
      else if (params.edges.length) {
        const e = edgeById[params.edges[0]];
        showEdge(params.edges[0]); highlight([e.from, e.to]);
      } else { resetPanel(); unhighlight(); }
    });

    /* ---------- toolbar ---------- */
    let physicsOn = DATA.physics;
    const pbtn = root.querySelector(".wn-physics");
    pbtn.textContent = "Physics: " + (physicsOn ? "ON" : "OFF");
    pbtn.onclick = () => {
      physicsOn = !physicsOn;
      network.setOptions({ physics: { enabled: physicsOn } });
      pbtn.textContent = "Physics: " + (physicsOn ? "ON" : "OFF");
    };
    root.querySelector(".wn-fit").onclick = () => network.fit({ animation: true });
    root.querySelector(".wn-clear").onclick = () => {
      network.unselectAll(); resetPanel(); unhighlight();
    };
    network.once("stabilizationIterationsDone", () => {
      if (!DATA.physics) network.setOptions({ physics: { enabled: false } });
    });

    return { network, nodes, edges, showNode, showEdge, highlight, unhighlight, refreshTheme };
  }

  global.mountNetwork = mountNetwork;
})(window);
