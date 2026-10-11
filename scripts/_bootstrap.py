"""Make `app` importable whether scripts run in the container (/app) or from a checkout."""

import sys
from pathlib import Path

try:
    import app  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
