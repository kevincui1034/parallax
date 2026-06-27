"""Vercel entrypoint — exposes the FastAPI ASGI app at the project root.

Vercel's Python runtime auto-detects the `app` variable and routes all requests
(/, /health, /api/*, /v1/*, /mcp) to this single function.
"""

from app.main import app  # noqa: F401
