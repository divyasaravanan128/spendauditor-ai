"""Settings in one place. Everything comes from environment variables (.env) with safe defaults."""
from __future__ import annotations

import os
from pathlib import Path

try:  # python-dotenv is optional: plain environment variables work too
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
POLICIES = DATA / "policies"
DB_PATH = Path(os.getenv("SPENDAUDITOR_DB", DATA / "spendauditor.db"))  # the bill being analysed
LIVE_DB_PATH = DATA / "spendauditor.db"  # where the app logs its own AI calls (always the main database)
UPLOADS = DATA / "uploads"
ANSWER_KEY = DATA / "answer_key.json"
PRICES_FILE = RAW / "litellm_prices.json"

MODEL = os.getenv("SPENDAUDITOR_MODEL", "claude-haiku-5-5")
WRITER_MODEL = os.getenv("SPENDAUDITOR_WRITER_MODEL", MODEL)
DAILY_BUDGET_USD = float(os.getenv("SPENDAUDITOR_DAILY_BUDGET_USD", "1.00"))
WEB_SEARCH_ENABLED = os.getenv("SPENDAUDITOR_WEB_SEARCH", "false").lower() == "true"
WEB_SEARCH_PRICE_USD = 10 / 1000  # Anthropic: $10 per 1,000 searches

# The tables agents may read. Anything else is refused by the SQL safety layer.
READABLE_TABLES = {"charges", "budgets", "events", "live_charges", "real_sample"}
