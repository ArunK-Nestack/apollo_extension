"""
Root entrypoint proxying backend.api:app.
Allows running `uvicorn api:app` directly from the repository root.
"""
from backend.api import app

__all__ = ["app"]
