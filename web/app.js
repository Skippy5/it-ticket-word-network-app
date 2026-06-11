/* Decoupled frontend controller.
 *
 * Talks to the FastAPI backend (service.py engine) and renders with the shared
 * mountNetwork() from /assets/network.js — the same renderer the Streamlit
 * build uses, so the graph + drill-in behave identically.
 *
 * Host this anywhere static. To point at an API on another origin, set
 * window.WORDNET_API_BASE before this script loads.
 */
(function () {
  "use strict";

  const API = (window.WORDNET_API_BASE || "").replace(/\/$/, "");
  const $ = (sel) => document.querySelector(sel);

  const state = {
    config: null,
    datasetId: null,
    textColumns: [],
    filters: {},               // { column: [values] }
    params: {},
    extraStopwords: "",
    synonyms: "",
    urlTemplate: "",
    lastResp: null,
    filterEls: {},             // column -> { details, list }
    filterCols: [],
  };

  /* ------------------------------------------------------------------ */
  /* API helpers                                                         */
  /* ------------------------------------------------------------------ */
  async function api(path, body, method) {
    const opts = { method: method || (body ? "POST" : "GET"),
                   headers: { "Content-Type": "application/json" } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(API + path, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) {}
      throw new Error(detail);
    }
    return res.json();
  }

  let busyDepth = 0;
  function busy(on) {
    busyDepth += on ? 1 : -1;
    $("#busy").hidden = busyDepth <= 0;
  }

  /* ------------------------------------------------------------------ */
  /* Init                                                                */
  /* ------------------------------------------------------------------ */
  async function init() {
    busy(true);
    try {
      const [cfg, ds] = await Promise.all([api("/api/config"), api("/api/datasets")]);
      state.config = cfg;
      state.params = defaultParams(cfg.defaults);
      state.extraStopwords = cfg.stopwords.join("\n");
      state.synonyms = Object.entries(cfg.synonyms).map(([k, v]) => k + " => " + v).join("\n");
      state.urlTemplate = cfg.url_template;

      $("#stopwords").value = state.extraStopwords;
      $("#synonyms").value = state.synonyms;
      $("#url-template").value = state.urlTemplate;

      buildParamControls(cfg.defaults);
      wireStaticEvents();

      const sel = $("#dataset");
      sel.innerHTML = "";
      ds.datasets.forEach((d) => {
        const o = document.createElement("option");
        o.value = d.id; o.textContent = d.label + " (" + d.rows + ")";
        o.dataset.textColumns = JSON.stringify(d.text_columns);
        sel.appendChild(o);
      });
      if (ds.datasets.length) {
        sel.value = ds.datasets[0].id;
        await onDatasetChange();
      }
    } catch (e) {
      showMessage("Could not reach the API: " + e.message);
    } finally {
      busy(false);
    }
  }

  function defaultParams(d) {
    return {
      phrase_detection: d.phrase_detection,
      weighting: d.weighting,
      cooc_scope: d.cooc_scope,
      window_size: d.window_size,
      min_term_freq: d.min_term_freq,
      min_edge_weight: d.weighting === "pmi" ? d.min_edge_weight_pmi : d.min_edge_weight,
      max_nodes: d.max_nodes,
      max_edges_per_node: d.max_edges_per_node,
      louvain_resolution: d.louvain_resolution,
      physics: d.physics,
    };
  }

  /* ------------------------------------------------------------------ */
  /* Dataset / text columns                                              */
  /* ------------------------------------------------------------------ */
  async function onDatasetChange() {
    const opt = $("#dataset").selectedOptions[0];
    if (!opt) return;
    state.datasetId = opt.value;
    const tcols = JSON.parse(opt.dataset.textColumns || "[]");
    state.textColumns = tcols.slice();
    buildTextColumns(tcols, state.textColumns);
    state.filters = {};
    state.filterCols = [];
    $("#filters").innerHTML = "";
    state.filterEls = {};
    await recompute();
  }

  function buildTextColumns(all, selected) {
    const host = $("#text-columns");
    host.innerHTML = "";
    if (!all.length) { host.innerHTML = '<div class="hint">No text columns in this file.</div>'; return; }
    all.forEach((c) => {
      const lab = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.value = c; cb.checked = selected.includes(c);
      cb.onchange = () => {
        state.textColumns = Array.from(host.querySelectorAll("input:checked")).map((i) => i.value);
        recompute();
      };
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(c));
      host.appendChild(lab);
    });
  }

  /* ------------------------------------------------------------------ */
  /* Filters (custom cascading multi-selects)                            */
  /* ------------------------------------------------------------------ */
  function prettify(s) { return s.replace(/_/g, " "); }

  function ensureFilterSkeleton(cols) {
    if (sameArray(cols, state.filterCols)) return;
    state.filterCols = cols.slice();
    const host = $("#filters");
    host.innerHTML = "";
    state.filterEls = {};
    cols.forEach((col) => {
      const det = document.createElement("details");
      det.className = "ms";
      const sum = document.createElement("summary");
      sum.innerHTML = "<span>" + prettify(col) + "</span><span class='ms-count' data-col='" + col + "'></span>";
      const list = document.createElement("div");
      list.className = "ms-list";
      det.appendChild(sum); det.appendChild(list);
      host.appendChild(det);
      state.filterEls[col] = { details: det, list: list };
    });
  }

  function updateFilterLists(options) {
    state.filterCols.forEach((col) => {
      const refs = state.filterEls[col];
      if (!refs) return;
      const opts = options[col] || [];
      const selected = state.filters[col] || [];
      refs.list.innerHTML = "";
      if (!opts.length) {
        refs.list.innerHTML = '<div class="ms-empty">no values in scope</div>';
      } else {
        opts.forEach((v) => {
          const lab = document.createElement("label");
          const cb = document.createElement("input");
          cb.type = "checkbox"; cb.value = v; cb.checked = selected.includes(v);
          cb.onchange = () => onFilterToggle(col);
          lab.appendChild(cb);
          lab.appendChild(document.createTextNode(v));
          refs.list.appendChild(lab);
        });
      }
      const count = $(".ms-count[data-col='" + cssEscape(col) + "']");
      if (count) count.textContent = selected.length ? "(" + selected.length + ")" : "";
    });
  }

  function onFilterToggle(col) {
    const refs = state.filterEls[col];
    const vals = Array.from(refs.list.querySelectorAll("input:checked")).map((i) => i.value);
    if (vals.length) state.filters[col] = vals; else delete state.filters[col];
    recompute();
  }

  /* ------------------------------------------------------------------ */
  /* Network parameter controls                                          */
  /* ------------------------------------------------------------------ */
  function buildParamControls(d) {
    const host = $("#params");
    host.innerHTML = "";
    host.appendChild(toggle("Phrase detection (bigrams)", "phrase_detection"));
    host.appendChild(select("Edge weighting", "weighting", ["count", "pmi"], () => {
      // reset edge-weight floor to the weighting-appropriate default
      state.params.min_edge_weight =
        state.params.weighting === "pmi" ? d.min_edge_weight_pmi : d.min_edge_weight;
      const inp = $("#p-min_edge_weight"); if (inp) inp.value = state.params.min_edge_weight;
    }));
    host.appendChild(select("Co-occurrence scope", "cooc_scope", ["document", "window"]));
    host.appendChild(range("Window size", "window_size", 3, 25, 1));
    host.appendChild(range("Min term frequency", "min_term_freq", 1, 20, 1));
    host.appendChild(number("Min edge weight", "min_edge_weight", 0, 50, 0.1));
    host.appendChild(range("Max nodes", "max_nodes", 10, 250, 5));
    host.appendChild(range("Max edges / node (0=all)", "max_edges_per_node", 0, 30, 1));
    host.appendChild(range("Louvain resolution", "louvain_resolution", 0.4, 2.5, 0.1));
    host.appendChild(toggle("Physics on load", "physics"));
  }

  function commitParam(key, value) { state.params[key] = value; recompute(); }

  function toggle(label, key) {
    const row = document.createElement("label");
    row.className = "toggle-row";
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = !!state.params[key];
    cb.onchange = () => commitParam(key, cb.checked);
    row.appendChild(cb); row.appendChild(document.createTextNode(label));
    return row;
  }
  function select(label, key, options, after) {
    const row = document.createElement("div"); row.className = "param-row";
    row.appendChild(labelEl(label));
    const sel = document.createElement("select"); sel.className = "ctrl";
    options.forEach((o) => {
      const op = document.createElement("option"); op.value = o; op.textContent = o; sel.appendChild(op);
    });
    sel.value = state.params[key];
    sel.onchange = () => { state.params[key] = sel.value; if (after) after(); recompute(); };
    row.appendChild(sel);
    return row;
  }
  function range(label, key, min, max, step) {
    const row = document.createElement("div"); row.className = "param-row";
    const line = document.createElement("div"); line.className = "row-line";
    const lab = document.createElement("label"); lab.textContent = label;
    const val = document.createElement("span"); val.className = "val"; val.textContent = state.params[key];
    line.appendChild(lab); line.appendChild(val);
    const inp = document.createElement("input");
    inp.type = "range"; inp.min = min; inp.max = max; inp.step = step; inp.value = state.params[key];
    inp.id = "p-" + key;
    inp.oninput = () => { val.textContent = inp.value; };
    inp.onchange = () => commitParam(key, step < 1 ? parseFloat(inp.value) : parseInt(inp.value, 10));
    row.appendChild(line); row.appendChild(inp);
    return row;
  }
  function number(label, key, min, max, step) {
    const row = document.createElement("div"); row.className = "param-row";
    row.appendChild(labelEl(label));
    const inp = document.createElement("input");
    inp.type = "number"; inp.className = "ctrl"; inp.min = min; inp.max = max; inp.step = step;
    inp.value = state.params[key]; inp.id = "p-" + key;
    inp.onchange = () => commitParam(key, parseFloat(inp.value));
    row.appendChild(inp);
    return row;
  }
  function labelEl(t) { const l = document.createElement("label"); l.textContent = t; return l; }

  /* ------------------------------------------------------------------ */
  /* Static events                                                       */
  /* ------------------------------------------------------------------ */
  function wireStaticEvents() {
    $("#dataset").onchange = onDatasetChange;
    $("#reset-filters").onclick = () => {
      state.filters = {};
      updateFilterLists(lastOptions());
      recompute();
    };
    $("#apply-text").onclick = () => {
      state.extraStopwords = $("#stopwords").value;
      state.synonyms = $("#synonyms").value;
      state.urlTemplate = $("#url-template").value;
      recompute();
    };
    $("#upload").onchange = onUpload;
    document.querySelectorAll(".exp-btn").forEach((b) => {
      b.onclick = () => doExport(b.dataset.export);
    });
  }

  async function onUpload(ev) {
    const files = ev.target.files;
    if (!files || !files.length) return;
    busy(true);
    try {
      const fd = new FormData();
      Array.from(files).forEach((f) => fd.append("files", f));
      const res = await fetch(API + "/api/upload", { method: "POST", body: fd });
      if (!res.ok) {
        let d = res.statusText; try { d = (await res.json()).detail || d; } catch (e) {}
        throw new Error(d);
      }
      const info = await res.json();
      const sel = $("#dataset");
      const o = document.createElement("option");
      o.value = info.dataset_id;
      o.textContent = "⤴ " + info.label + " (" + info.rows + ")";
      o.dataset.textColumns = JSON.stringify(info.text_columns);
      sel.appendChild(o); sel.value = info.dataset_id;
      $("#upload-status").textContent = "Loaded " + info.rows + " incidents.";
      await onDatasetChange();
    } catch (e) {
      $("#upload-status").textContent = "Upload failed: " + e.message;
    } finally {
      busy(false);
    }
  }

  /* ------------------------------------------------------------------ */
  /* Recompute + render                                                  */
  /* ------------------------------------------------------------------ */
  let pending = 0;
  async function recompute() {
    if (!state.datasetId) return;
    const seq = ++pending;
    busy(true);
    try {
      const resp = await api("/api/network", {
        dataset_id: state.datasetId,
        filters: state.filters,
        text_columns: state.textColumns,
        params: state.params,
        extra_stopwords: state.extraStopwords,
        synonyms: state.synonyms,
        url_template: state.urlTemplate,
      });
      if (seq !== pending) return;          // a newer request superseded this one
      state.lastResp = resp;
      render(resp);
    } catch (e) {
      showMessage("Compute failed: " + e.message);
    } finally {
      busy(false);
    }
  }

  function lastOptions() {
    return (state.lastResp && state.lastResp.filter_options) || {};
  }

  function render(resp) {
    const s = resp.stats;
    $("#scope").innerHTML = "Showing <b>" + s.in_scope + "</b> of <b>" + s.total +
      "</b> incidents — network computed on exactly this population.";
    $("#m-scope").textContent = s.in_scope;
    $("#m-nodes").textContent = s.n_nodes;
    $("#m-edges").textContent = s.n_edges;
    $("#m-clusters").textContent = s.n_clusters;

    // cascading filters
    const cols = Object.keys(resp.filter_options || {});
    ensureFilterSkeleton(cols);
    updateFilterLists(resp.filter_options || {});

    // message / network
    if (resp.message) showMessage(resp.message); else hideMessage();
    const host = $("#network");
    if (resp.payload && resp.payload.nodes.length) {
      const h = host.clientHeight > 80 ? host.clientHeight : 600;
      // controller is exposed on window.wordnet so an embedding page can drive
      // the graph (e.g. wordnet.showNode("outlook")) and for testing.
      state.controller = window.wordnet = window.mountNetwork(host, resp.payload, { height: h });
    } else {
      host.innerHTML = "";
      state.controller = window.wordnet = null;
    }

    renderClusters(resp.clusters || []);
  }

  function renderClusters(clusters) {
    const host = $("#clusters");
    if (!clusters.length) { host.innerHTML = '<div class="hint">No clusters.</div>'; return; }
    const palette = clusterPalette();
    let html = "<table><thead><tr><th>Cluster</th><th>Terms</th><th>Top terms</th></tr></thead><tbody>";
    clusters.forEach((c) => {
      const color = palette[c.community % palette.length];
      html += "<tr><td><span class='cluster-dot' style='background:" + color + "'></span>C" +
        c.community + "</td><td>" + c.size + "</td><td>" +
        escapeHtml(c.top_terms.join(", ")) + "</td></tr>";
    });
    html += "</tbody></table>";
    host.innerHTML = html;
  }

  // mirror config.PALETTE (kept in sync with the payload node colors)
  function clusterPalette() {
    const fromNodes = {};
    if (state.lastResp && state.lastResp.payload) {
      state.lastResp.payload.legend.forEach((l) => (fromNodes[l.id] = l.color));
    }
    return Object.keys(fromNodes).length
      ? Object.keys(fromNodes).sort((a, b) => a - b).map((k) => fromNodes[k])
      : ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#b07aa1", "#76b7b2",
         "#edc948", "#ff9da7", "#9c755f", "#bab0ac"];
  }

  /* ------------------------------------------------------------------ */
  /* Exports (client-side)                                               */
  /* ------------------------------------------------------------------ */
  async function doExport(kind) {
    const resp = state.lastResp;
    if (!resp) return;
    if (kind === "nodes") {
      download("network_nodes.csv", toCSV(resp.exports.nodes), "text/csv");
    } else if (kind === "edges") {
      download("network_edges.csv", toCSV(resp.exports.edges), "text/csv");
    } else if (kind === "graph") {
      const g = { nodes: resp.payload.nodes, edges: resp.payload.edges, tickets: resp.payload.tickets };
      download("network_graph.json", JSON.stringify(g, null, 2), "application/json");
    } else if (kind === "incidents") {
      busy(true);
      try {
        const res = await fetch(API + "/api/incidents.csv", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dataset_id: state.datasetId, filters: state.filters }),
        });
        const text = await res.text();
        download("incidents_in_scope.csv", text, "text/csv");
      } catch (e) {
        showMessage("Export failed: " + e.message);
      } finally { busy(false); }
    }
  }

  function toCSV(records) {
    if (!records || !records.length) return "";
    const cols = Object.keys(records[0]);
    const esc = (v) => {
      v = v == null ? "" : String(v);
      return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
    };
    const lines = [cols.join(",")];
    records.forEach((r) => lines.push(cols.map((c) => esc(r[c])).join(",")));
    return lines.join("\n");
  }
  function download(name, text, mime) {
    const blob = new Blob([text], { type: mime });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click();
    document.body.removeChild(a); URL.revokeObjectURL(a.href);
  }

  /* ------------------------------------------------------------------ */
  /* Misc helpers                                                        */
  /* ------------------------------------------------------------------ */
  function showMessage(t) { const m = $("#message"); m.textContent = t; m.hidden = false; }
  function hideMessage() { $("#message").hidden = true; }
  function sameArray(a, b) { return a.length === b.length && a.every((x, i) => x === b[i]); }
  function escapeHtml(s) { return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
  function cssEscape(s) { return s.replace(/'/g, "\\'"); }

  init();
})();
