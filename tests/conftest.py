"""Tests run without an API key: data and metrics are real, model calls are replaced by stubs."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session", autouse=True)
def database():
    from spendauditor.config import DB_PATH

    if not DB_PATH.exists():
        for script in ("fetch_data.py", "generate_history.py", "build_db.py"):
            subprocess.run([sys.executable, str(ROOT / "scripts" / script)], check=True)
    return DB_PATH
