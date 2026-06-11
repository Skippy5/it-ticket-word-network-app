"""Vercel serverless entrypoint.

Vercel's Python runtime turns api/index.py into a serverless function, and the
rewrite in vercel.json routes every /api/* request here. FastAPI is ASGI,
which the runtime speaks natively — so the exact same `app` serves locally
(uvicorn), in a container (Dockerfile), and on Vercel.

The project root is on sys.path when the function is invoked, so root-level
modules (server.py and the engine) import directly.
"""

from server import app  # noqa: F401
