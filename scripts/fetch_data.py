"""Download the two real, public inputs into data/raw/.

1. FOCUS sample data (FinOps Foundation, CC BY 4.0): 10,000 anonymized real billing rows, September 2024.
2. LiteLLM model price file (MIT): current per-token prices for thousands of models.

Run once:  python scripts/fetch_data.py
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spendauditor.config import RAW  # noqa: E402

SOURCES = {
    "focus_sample_10000.csv": "https://raw.githubusercontent.com/FinOps-Open-Cost-and-Usage-Spec/"
    "FOCUS-Sample-Data/main/FOCUS-1.0/focus_sample_10000.csv",
    "litellm_prices.json": "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
}


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        target = RAW / name
        if target.exists():
            print(f"already have {name}")
            continue
        print(f"downloading {name} ...")
        urllib.request.urlretrieve(url, target)
        print(f"  saved {target.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
