# -*- coding: utf-8 -*-
"""
Pytest Central Configuration & Subproject Environment Isolation
==============================================================
Automatically isolates the 'app' namespace between 'freshsales_agent'
and 'millionverifier_agent_step1' for unit and integration tests.
"""

from __future__ import annotations

import sys
from pathlib import Path
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FS_AGENT_DIR = PROJECT_ROOT / "freshsales_agent"
MV_DIR = PROJECT_ROOT / "millionverifier_agent_step1"


def isolate_app_namespace(target_dir: Path) -> None:
    """Purge cached 'app' modules from sys.modules and place target_dir at the front of sys.path."""
    # Remove conflicting dirs
    for d in [FS_AGENT_DIR, MV_DIR]:
        if str(d) in sys.path:
            sys.path.remove(str(d))

    # Purge any cached 'app' modules
    if "app" in sys.modules:
        for k in list(sys.modules.keys()):
            if k == "app" or k.startswith("app."):
                del sys.modules[k]

    # Insert target directory at front of sys.path
    sys.path.insert(0, str(target_dir))
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(1, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def auto_isolate_agent_namespace(request):
    """Automatically isolate the correct 'app' package based on the running test module."""
    module_name = getattr(request.module, "__name__", "")
    if "freshsales" in module_name:
        isolate_app_namespace(FS_AGENT_DIR)
        try:
            import app.services.batch_processor
        except Exception:
            pass
    elif "millionverifier" in module_name:
        isolate_app_namespace(MV_DIR)
    yield
