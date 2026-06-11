# Container for the decoupled build: FastAPI compute API + the static web
# frontend served from one image. Drops onto AWS ECS/Fargate, App Runner, or
# Azure App Service / Container Apps. Honors $PORT (injected by many PaaS).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Install deps first for layer caching (requirements.txt is the slim set —
# the Streamlit front end is a separate extra not needed in this image).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + engine + assets + frontend + sample data
COPY config.py english_stopwords.py preprocess.py cooccurrence.py \
     clustering.py drilldown.py viz.py service.py server.py api.py ./
COPY assets/ ./assets/
COPY web/ ./web/
COPY data/ ./data/

EXPOSE 8000

# Lock down cross-origin embedding in production via, e.g.:
#   -e WORDNET_CORS_ORIGINS="https://portal.corp,https://intranet.corp"
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
