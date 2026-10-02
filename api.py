"""
Root entrypoint proxying backend.api:app.
Allows running `uvicorn api:app` or `python api.py` directly from the repository root.
"""
import uvicorn
from backend.api import app

__all__ = ["app"]

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print(">>> [ContactChecker API] Starting Lead Processing Engine (Port 8000)")
    print("=" * 70 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
