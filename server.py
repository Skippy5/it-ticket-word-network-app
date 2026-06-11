"""FastAPI backend for the decoupled web frontend.

Stateless compute API (reuses service.py / the existing engine) plus optional
static hosting of the `web/` frontend. The SAME app object serves three ways:

    local:      uvicorn server:app --reload --port 8000
    container:  Dockerfile (ECS/Fargate, App Runner, Azure Container Apps)
    Vercel:     api/index.py imports `app`; vercel.json routes /api/* to it

Statelessness contract (required for serverless): every endpoint can operate
from the request alone. Uploaded CSVs are parsed and their rows returned to
the client, which sends them back inline (`records`) with each compute
request — so a different serverless instance can always serve the next call.
The in-memory upload cache below is just a fast path for long-running
deployments; losing it is never an error.

Endpoints (all JSON unless noted):
    GET  /api/health
    GET  /api/config                       defaults, stopwords, synonyms, etc.
    GET  /api/datasets                     bundled sample datasets
    POST /api/upload        (multipart)    parse CSV(s) -> dataset_id + records
    POST /api/options       {dataset_id|records, filters}    cascading options
    POST /api/network       {dataset_id|records, filters, params...} full payload
    POST /api/incidents.csv {dataset_id|records, filters}    in-scope CSV
    GET  /                                 serves web/index.html (if present)
"""

from __future__ import annotations

import io
import os
import uuid
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import service

BASE = Path(__file__).parent
DATA_DIR = BASE / "data"
WEB_DIR = BASE / "web"

app = FastAPI(title="IT Ticket Word Network API", version="1.1")

# CORS: allow the static frontend to call this API from another origin (e.g. an
# internal portal page or an S3/Blob-hosted bundle). Lock down via env var in
# production: WORDNET_CORS_ORIGINS="https://portal.corp,https://intranet.corp"
_origins = os.environ.get("WORDNET_CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _origins == "*" else [o.strip() for o in _origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Dataset registry: bundled samples (read once) + ephemeral upload cache
# ---------------------------------------------------------------------------

_samples: dict[str, pd.DataFrame] = {}
_uploads: dict[str, pd.DataFrame] = {}


def _read_csv(raw: bytes) -> pd.DataFrame:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=enc, dtype=str,
                               on_bad_lines="skip", keep_default_na=False)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(io.BytesIO(raw), encoding="latin-1", dtype=str,
                       on_bad_lines="skip", keep_default_na=False)


def _load_samples() -> None:
    if not DATA_DIR.exists():
        return
    for path in sorted(DATA_DIR.glob("*.csv")):
        try:
            df = service.normalize_dataframe(_read_csv(path.read_bytes()))
            if config.ID_COLUMN in df.columns:
                _samples[path.name] = df
        except Exception:
            continue


_load_samples()


def _records_to_df(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records).astype(str)
    return service.normalize_dataframe(df)


def _resolve_dataset(dataset_id: str | None, records: list[dict] | None) -> pd.DataFrame:
    """Inline records win (the stateless path); otherwise look up by id."""
    if records:
        return _records_to_df(records)
    if dataset_id:
        if dataset_id in _uploads:
            return _uploads[dataset_id]
        if dataset_id in _samples:
            return _samples[dataset_id]
        if dataset_id.startswith("upload:"):
            raise HTTPException(
                status_code=410,
                detail="Uploaded dataset no longer cached on this instance "
                       "(stateless hosting). Re-upload the CSV, or use a client "
                       "that sends the rows inline with each request.")
    raise HTTPException(status_code=404, detail=f"Unknown dataset '{dataset_id}'")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class OptionsRequest(BaseModel):
    dataset_id: str | None = None
    records: list[dict] | None = None     # inline rows (stateless upload path)
    filters: dict = {}


class NetworkRequest(BaseModel):
    dataset_id: str | None = None
    records: list[dict] | None = None     # inline rows (stateless upload path)
    filters: dict = {}
    text_columns: list[str] | None = None
    params: dict | None = None
    extra_stopwords: object | None = None
    synonyms: object | None = None
    url_template: str = config.TICKET_URL_TEMPLATE


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "samples": list(_samples)}


@app.get("/api/config")
def get_config() -> dict:
    return {
        "defaults": config.DEFAULTS,
        "text_columns": config.TEXT_COLUMNS,
        "filter_columns": config.FILTER_COLUMNS,
        "geo_cascade": config.GEO_CASCADE,
        "stopwords": config.IT_STOPWORDS,
        "synonyms": config.SYNONYMS,
        "url_template": config.TICKET_URL_TEMPLATE,
    }


@app.get("/api/datasets")
def list_datasets() -> dict:
    out = []
    for name, df in _samples.items():
        out.append({
            "id": name,
            "label": name,
            "rows": len(df),
            "text_columns": service.available_text_columns(df),
        })
    return {"datasets": out}


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)) -> dict:
    frames = []
    names = []
    for f in files:
        raw = await f.read()
        try:
            df = service.normalize_dataframe(_read_csv(raw))
        except Exception as exc:
            raise HTTPException(status_code=400,
                                detail=f"Could not read {f.filename}: {exc}")
        frames.append(df)
        names.append(f.filename)
    if not frames:
        raise HTTPException(status_code=400, detail="No files uploaded.")
    df = pd.concat(frames, ignore_index=True)
    if config.ID_COLUMN not in df.columns:
        raise HTTPException(
            status_code=400,
            detail=f"Required column '{config.ID_COLUMN}' missing. "
                   f"Found: {', '.join(df.columns)}")
    dataset_id = "upload:" + uuid.uuid4().hex[:12]
    _uploads[dataset_id] = df
    # cap memory: keep only the most recent few uploads
    if len(_uploads) > 8:
        for k in list(_uploads)[:-8]:
            _uploads.pop(k, None)
    # Return the parsed rows so the client can resend them inline with each
    # compute request — this is what makes uploads work on serverless hosting,
    # where the next request may hit a fresh instance without this cache.
    public = df.drop(columns=[c for c in ("__opened_dt",) if c in df.columns])
    return {
        "dataset_id": dataset_id,
        "label": " + ".join(names),
        "rows": len(df),
        "text_columns": service.available_text_columns(df),
        "records": public.to_dict("records"),
    }


@app.post("/api/options")
def options(req: OptionsRequest) -> dict:
    df = _resolve_dataset(req.dataset_id, req.records)
    return {
        "filter_options": service.filter_options(df, req.filters),
        "text_columns": service.available_text_columns(df),
        "total": len(df),
        "in_scope": len(service.apply_filters(df, req.filters)),
    }


@app.post("/api/network")
def network(req: NetworkRequest) -> dict:
    df = _resolve_dataset(req.dataset_id, req.records)
    return service.compute_network(
        df,
        filters=req.filters,
        text_columns=req.text_columns,
        opts=req.params,
        extra_stopwords=req.extra_stopwords,
        synonyms=req.synonyms,
        url_template=req.url_template or config.TICKET_URL_TEMPLATE,
    )


@app.post("/api/incidents.csv", response_class=PlainTextResponse)
def incidents_csv(req: OptionsRequest) -> str:
    """Filtered incident list as CSV (the in-scope population, helper cols dropped)."""
    df = _resolve_dataset(req.dataset_id, req.records)
    scoped = service.apply_filters(df, req.filters)
    scoped = scoped.drop(columns=[c for c in ("__opened_dt",) if c in scoped.columns])
    return scoped.to_csv(index=False)


# ---------------------------------------------------------------------------
# Static frontend (single-container / local hosting; on Vercel the platform
# serves /web and /assets directly and only /api/* reaches this app)
# ---------------------------------------------------------------------------

if WEB_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(BASE / "assets")), name="assets")
    app.mount("/web", StaticFiles(directory=str(WEB_DIR)), name="web")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(WEB_DIR / "index.html"))
