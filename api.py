"""
Root entrypoint proxying backend.api:app.
Allows running `uvicorn api:app` or `python api.py` directly from the repository root.
"""
import os

import uvicorn
from backend.api import app

__all__ = ["app"]

if __name__ == "__main__":
    app_env = os.getenv("APP_ENV", "local").strip().lower()
    api_host = os.getenv("API_HOST", "127.0.0.1").strip() or "127.0.0.1"
    api_port = int(os.getenv("API_PORT", "8000").strip() or "8000")
    print("\n" + "=" * 70)
    print(f">>> [ContactChecker API] Starting Lead Processing Engine ({api_host}:{api_port})")
    print("=" * 70 + "\n")
    uvicorn.run("api:app", host=api_host, port=api_port, reload=app_env == "local")
