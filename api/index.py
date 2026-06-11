"""Vercel serverless entrypoint.

Vercel's Python runtime turns api/index.py into a serverless function, and the
rewrites in vercel.json route every /api/* request here. FastAPI is ASGI,
which the runtime speaks natively — so the exact same `app` serves locally
(uvicorn server:app), in a container (Dockerfile), and on Vercel.

The engine modules (server.py and friends) live at the project root, one level
up from this file. We add that root to sys.path explicitly so `from server
import app` resolves no matter what working directory / path the platform
invokes the function with.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from server import app  # noqa: E402,F401
