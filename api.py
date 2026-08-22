"""
Root entrypoint proxying backend.api:app.
Allows running `uvicorn api:app` directly from the repository root.
"""
from backend.api import *

__all__ = ["app"]
