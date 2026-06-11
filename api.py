"""Back-compat shim: the FastAPI app moved to server.py.

Keeps `uvicorn api:app` working from older docs/scripts. (The sibling `api/`
directory is the Vercel serverless entrypoint; a root-level api.py module
takes import precedence over a same-named namespace directory, so
`import api` still resolves here.)
"""

from server import app  # noqa: F401
