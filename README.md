# IT Ticket Word Co-occurrence Network

Interactive, filterable word co-occurrence network for IT tickets. Upload ticket
CSVs (ServiceNow-style export), the app cleans the free text, collapses
synonyms/abbreviations, detects multi-word phrases, builds a term co-occurrence
graph (count or positive-PMI weighted), clusters it with Louvain, and renders a
force-directed network where **every node and edge drills back to the exact
incident numbers** that produced it.

The analytics engine is plain Python and is reused by **three interchangeable
front ends** — pick whichever fits where you're hosting:

| Front end | Best for | Live filter/upload? | Hosting |
|---|---|---|---|
| **Streamlit** (`app.py`) | quickest local/exploratory use | yes | a persistent process (Streamlit Cloud, Render, a VM, or a container) |
| **Decoupled web** (`server.py` + `web/`) | **Vercel, internal AWS/Azure hosting & embedding into pages** | yes | Vercel (zero-config via `vercel.json`), or static frontend anywhere + a containerized API |
| **Standalone HTML** (`cli.py`) | drop a snapshot onto any page / S3 / Blob / SharePoint | no (fixed snapshot) | none — a single self-contained file |

The decoupled web build has a **light/dark mode toggle** (top right); the saved
preference persists, defaulting to your OS color scheme.

All three render the **same** vis-network graph and drill-in, because they share
one renderer (`assets/network.js` + `assets/network.css`) and one payload
builder (`viz.build_graph_payload`).

## Setup

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

`requirements.txt` is deliberately slim (pandas/numpy/networkx/FastAPI — no
scipy or scikit-learn) so the same set deploys to serverless hosts. The
Streamlit front end is an extra:

```powershell
.venv\Scripts\pip install -r requirements-streamlit.txt
```

Optional (better lemmatization — the app works without it via a built-in
corpus-checked rule lemmatizer):

```powershell
.venv\Scripts\pip install spacy
.venv\Scripts\python -m spacy download en_core_web_sm
```

## Run the app

```powershell
.venv\Scripts\streamlit run app.py
```

Then open http://localhost:8501 (Streamlit prints the URL). Pick a bundled
sample under **Sample dataset** (in `data/`) or upload your own CSV(s).

## Standalone HTML (no server)

```powershell
.venv\Scripts\python cli.py data\it_tickets_large.csv --out network.html
.venv\Scripts\python cli.py data\it_tickets_messy.csv --weighting pmi --max-nodes 80 --out messy.html
```

`network.html` is fully self-contained (vis-network is inlined) — share it by
sending the file.

## Decoupled web build (API + static frontend) — for internal AWS/Azure hosting

This is the build to use when you need to host on internal infrastructure and/or
**embed the network into existing pages/portals**. It splits cleanly:

- **`web/`** — a static frontend (HTML/JS/CSS + vis.js). Pure files: host them on
  S3 + CloudFront, Azure Blob static website, an internal web server, or embed
  into an existing page. The graph + drill-in run entirely client-side.
- **`server.py`** — a FastAPI compute service (reuses the engine via `service.py`).
  It does the preprocessing / co-occurrence / PMI / Louvain and returns graph
  JSON. Runs as a container behind your load balancer / API gateway / SSO, or
  as a Vercel serverless function (`api/index.py` imports the same app).

### Run locally (one process serves both)

```powershell
.venv\Scripts\python -m uvicorn server:app --port 8000
```

Open http://localhost:8000 — the API also serves the `web/` frontend, so a
single container is enough for a simple deployment.

### Run as a container

```bash
docker build -t ticket-word-network .
docker run -p 8000:8000 ticket-word-network
# lock down cross-origin embedding in production:
#   -e WORDNET_CORS_ORIGINS="https://portal.corp,https://intranet.corp"
```

The image honors `$PORT` (set by many PaaS) and binds `0.0.0.0`.

### Deploy to Vercel

The repo is Vercel-ready — import it and deploy, no settings needed:

1. [vercel.com/new](https://vercel.com/new) → import the GitHub repo.
2. Leave **Framework Preset = Other** and all build settings empty.
3. Deploy.

What happens: Vercel serves `web/` and `assets/` as static files, and
`vercel.json` routes `/` to the frontend and every `/api/*` call to a Python
serverless function (`api/index.py`) wrapping the same FastAPI app.
`requirements.txt` is slim by design so the function bundle stays well under
Vercel's 250 MB limit.

Serverless caveats (by design, already handled):

- **Uploads are stateless**: the parsed rows are returned to the browser and
  sent back inline with each compute request, so any instance can serve any
  call. Practical upload ceiling is a few MB of CSV (platform request-body
  limit ~4.5 MB); bigger files belong on the container deployment.
- **Cold starts**: the first request after idle takes a few extra seconds
  (pandas import + sample load); warm requests compute in ~1–3 s.

### Deploy to AWS / Azure

- **AWS:** push the image to ECR, run it on **ECS/Fargate** or **App Runner**
  (both take a container + port directly). Put it behind an ALB; use ALB OIDC or
  Cognito for SSO.
- **Azure:** push to ACR, run on **Azure Container Apps** or **App Service for
  Containers**; front it with Application Gateway / APIM and Entra ID for SSO.
- **Split hosting (most embeddable):** deploy only `api.py` as the container,
  and host `web/` + `assets/` as static files (S3 / Blob / your portal). In the
  page, set the API origin before `app.js` loads and let CORS allow it:

  ```html
  <script>window.WORDNET_API_BASE = "https://wordnet-api.corp";</script>
  ```

  An embedding page can also drive the graph after load via the exposed handle,
  e.g. `window.wordnet.showNode("outlook")`.

### API endpoints

| Method · path | Purpose |
|---|---|
| `GET /api/health` | liveness + loaded sample list |
| `GET /api/config` | defaults, stop words, synonym map, URL template |
| `GET /api/datasets` | bundled sample datasets |
| `POST /api/upload` | parse CSV(s) → `dataset_id` **+ the parsed rows** |
| `POST /api/options` | cascading filter option lists for a selection |
| `POST /api/network` | full graph payload + stats + clusters + export tables |
| `POST /api/incidents.csv` | filtered incident list as CSV |

> Every compute endpoint accepts either a `dataset_id` (bundled samples) or the
> rows inline as `records` — the frontend resends uploaded rows with each
> request, so the API needs no per-instance memory and runs unchanged on
> serverless hosting. (A best-effort in-memory upload cache still speeds up
> long-running container deployments.)

## How it works

```
CSV -> filter population -> per-ticket document (chosen text columns)
    -> clean (HTML/URLs/emails/timestamps/ticket numbers stripped, lowercased)
    -> tokenize -> synonym map (pwd->password, dl->distribution list, ...)
    -> lemmatize (spaCy if installed, else corpus-checked rules)
    -> seed + auto-detected phrases (print_queue, active_directory, ...)
    -> stop words (standard English + editable IT list)
    -> binary docs x terms sparse matrix -> X.T @ X co-occurrence (vectorized)
    -> count or positive-PMI edge weights -> pruning (min freq / weight / top-N)
    -> networkx graph -> Louvain communities (seeded) -> vis-network render
```

| File | Role |
|---|---|
| `preprocess.py` | cleaning, tokenizing, synonyms, lemmatization, phrases, stop words |
| `english_stopwords.py` | vendored standard English stop-word list (drops the sklearn dependency) |
| `cooccurrence.py` | numpy co-occurrence matrix (`X.T @ X`), PMI, pruning, graph build |
| `clustering.py` | Louvain communities (networkx built-in, seeded) |
| `drilldown.py` | term→incidents and edge→incidents lookups, export tables |
| `viz.py` | payload builder + self-contained HTML (inlines the shared assets) |
| `config.py` | stop words, synonym map, seed phrases, defaults, URL template |
| `service.py` | stateless orchestration (filter → pipeline → graph → payload) shared by the API |
| `app.py` | **Streamlit** front end: upload, filters, parameters, stats, exports |
| `server.py` | **FastAPI** compute API + optional static hosting of `web/` (`api.py` is a back-compat shim) |
| `api/index.py` | **Vercel** serverless entrypoint (imports the same FastAPI app) |
| `vercel.json` | Vercel routing: static `web/`+`assets/`, `/api/*` → the function |
| `web/` | **static frontend** (`index.html`, `app.js`, `styles.css`) for the decoupled build |
| `assets/` | shared renderer (`network.js`, `network.css`) + vendored `vis-network.min.js` |
| `cli.py` | one-shot standalone `network.html` generator |
| `Dockerfile` | container for the API + static frontend (ECS/Fargate, Container Apps) |
| `data/` | sample CSVs (small / large / multidept / messy) |

## Filters

Sidebar filters subset the ticket population **before** the network is computed,
so the picture always reflects exactly the selection. Geography cascades
country → state → location (picking a country narrows the state and location
options). More filters: business unit, category, subcategory, assignment group,
priority, status, and an `opened_at` date range. The header shows the live
"Showing N of M incidents" count; **Reset filters** clears everything.

## Drill-in / traceability

- **Click a node** → side panel lists every incident containing that term
  (ticket id, short description, priority/status/BU/location), each id copyable
  and linked via the configurable URL template
  (`https://…/incident.do?sysparm_query=number={ticket_id}`).
- **Click an edge** → the incidents where **both** terms co-occur — the evidence
  behind the connection (e.g. the `printer`–`paper_jam` edge lists the exact
  tickets behind that link).
- **Hover a node** → term, frequency, community, top neighbors.
- Selecting a node/edge fades everything outside its neighborhood; **Clear
  selection** (or click the background) restores.
- **Export tab** → nodes CSV (term, freq, community, all ticket ids), edges CSV
  (pair, weight, co-occurring ticket ids), filtered incident list CSV, and the
  graph JSON.

## Parameters (sidebar → "Network parameters")

Text columns · phrase detection on/off · co-occurrence scope (document or
sliding window + window size) · edge weighting (count vs positive PMI) · min
term frequency · min edge weight · max nodes · Louvain resolution · physics
on/off. Stop words and the synonym map are editable text areas under
"Stop words / synonyms"; the ticket URL template lives there too.

## Determinism

Louvain is seeded and communities are renumbered by size; the vis-network
layout uses a fixed `randomSeed`, so repeated runs on the same population and
parameters are comparable.

## Notes / known trade-offs

- vis-network is inlined from `assets/vis-network.min.js` (offline-friendly).
  Delete the file to fall back to the unpkg CDN.
- Justification for the JS layer: Python owns 100% of the analytics; vis.js was
  chosen over pyvis/streamlit-agraph because the drill-in requirement needs
  **edge-click** handlers and an in-component incident panel, which those
  wrappers don't expose.
- Tickets whose text becomes empty after cleaning simply contribute no terms —
  they are never dropped from the filtered incident export.
