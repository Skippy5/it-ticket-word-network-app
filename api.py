"""FastAPI backend for the decoupled web frontend.

Stateless compute API (reuses service.py / the existing engine) plus optional
static hosting of the `web/` frontend, so the whole thing can ship as ONE
container to ECS/Fargate or Azure Container Apps — or you can host the static
frontend separately (S3 / Azure Blob / an internal page) and point it at this
API via a base-URL override, with CORS handling cross-origin embedding.

Run locally:
    uvicorn api:app --reload --port 8000

Endpoints (all JSON unless noted):
    GET  /api/health
    GET  /api/config                       defaults, stopwords, synonyms, etc.
    GET  /api/datasets                     bundled sample datasets
    POST /api/upload        (multipart)    upload CSV(s) -> ephemeral dataset_id
    POST /api/options       {dataset_id, filters}            cascading options
    POST /api/network       {dataset_id, filters, params...} full graph payload
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

app = FastAPI(title="IT Ticket Word Network API", version="1.0")

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
# Dataset registry: bundled samples (read once) + ephemeral uploads (in-memory)
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


def _get_dataset(dataset_id: str) -> pd.DataFrame:
    if dataset_id in _uploads:
        return _uploads[dataset_id]
    if dataset_id in _samples:
        return _samples[dataset_id]
    raise HTTPException(status_code=404, detail=f"Unknown dataset '{dataset_id}'")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class OptionsRequest(BaseModel):
    dataset_id: str
    filters: dict = {}


class NetworkRequest(BaseModel):
    dataset_id: str
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
    return {
        "dataset_id": dataset_id,
        "label": " + ".join(names),
        "rows": len(df),
        "text_columns": service.available_text_columns(df),
    }


@app.post("/api/options")
def options(req: OptionsRequest) -> dict:
    df = _get_dataset(req.dataset_id)
    return {
        "filter_options": service.filter_options(df, req.filters),
        "text_columns": service.available_text_columns(df),
        "total": len(df),
        "in_scope": len(service.apply_filters(df, req.filters)),
    }


@app.post("/api/network")
def network(req: NetworkRequest) -> dict:
    df = _get_dataset(req.dataset_id)
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
    df = _get_dataset(req.dataset_id)
    scoped = service.apply_filters(df, req.filters)
    scoped = scoped.drop(columns=[c for c in ("__opened_dt",) if c in scoped.columns])
    return scoped.to_csv(index=False)


# ---------------------------------------------------------------------------
# Static frontend (optional single-container hosting)
# ---------------------------------------------------------------------------

if WEB_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(BASE / "assets")), name="assets")
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(WEB_DIR / "index.html"))
