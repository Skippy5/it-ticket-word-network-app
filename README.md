# IT Ticket Word Co-occurrence Network

Interactive, filterable word co-occurrence network for IT tickets. Upload ticket
CSVs (ServiceNow-style export), the app cleans the free text, collapses
synonyms/abbreviations, detects multi-word phrases, builds a term co-occurrence
graph (count or positive-PMI weighted), clusters it with Louvain, and renders a
force-directed network where **every node and edge drills back to the exact
incident numbers** that produced it.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
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
| `app.py` | Streamlit UI: upload, filters, parameters, stats, exports |
| `preprocess.py` | cleaning, tokenizing, synonyms, lemmatization, phrases, stop words |
| `cooccurrence.py` | sparse co-occurrence matrix, PMI, pruning, graph build |
| `clustering.py` | Louvain communities (networkx built-in, seeded) |
| `drilldown.py` | term→incidents and edge→incidents lookups, export tables |
| `viz.py` | vis-network HTML component (click/hover/drill-in/highlight) |
| `config.py` | stop words, synonym map, seed phrases, defaults, URL template |
| `cli.py` | one-shot standalone `network.html` generator |
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
