# Container for the decoupled build: FastAPI compute API + the static web
# frontend served from one image. Drops onto AWS ECS/Fargate, App Runner, or
# Azure App Service / Container Apps. Honors $PORT (injected by many PaaS).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Install deps first for layer caching. (Streamlit in requirements is unused by
# the API; drop it from requirements.txt if you want a slimmer image.)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + engine + assets + frontend + sample data
COPY config.py preprocess.py cooccurrence.py clustering.py drilldown.py \
     viz.py service.py api.py ./
COPY assets/ ./assets/
COPY web/ ./web/
COPY data/ ./data/

EXPOSE 8000

# Lock down cross-origin embedding in production via, e.g.:
#   -e WORDNET_CORS_ORIGINS="https://portal.corp,https://intranet.corp"
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}"]
