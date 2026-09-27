"""Refreshes `src/kraft/prices.json` from models.dev's Anthropic listing.

Not imported at runtime (`usage._prices` reads the committed JSON file, never
the network -- see that function's docstring): this is a one-shot script a
human runs (`just refresh-prices`) when Anthropic's pricing changes, the same
"generate, paste/commit, test the committed output" shape as `dev/gen_palette.py`.
"""

from __future__ import annotations

import datetime
import json
import sys
import urllib.request
from pathlib import Path

SOURCE = "https://models.dev/api.json"
PROVIDER = "anthropic"
OUT = Path(__file__).resolve().parents[1] / "src" / "kraft" / "prices.json"


def fetch(url: str = SOURCE) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 -- a fixed, known URL
        return json.load(resp)


def build(catalog: dict, provider: str = PROVIDER) -> dict:
    """`catalog` is models.dev's whole `api.json`; the result is the shape
    `prices.json` stores: per-model USD-per-million-token rates for one
    provider's models, plus where and when they were read."""
    models = catalog[provider]["models"]
    return {
        "source": SOURCE,
        "provider": provider,
        "snapshot_date": datetime.date.today().isoformat(),
        "note": (
            "USD per million tokens. cache_write is the cache-creation rate; "
            "cache_read is the cache-hit rate. Refresh with: just refresh-prices "
            "(dev/refresh_prices.py)."
        ),
        "models": {
            model_id: {
                "input": m["cost"]["input"],
                "output": m["cost"]["output"],
                "cache_read": m["cost"]["cache_read"],
                "cache_write": m["cost"]["cache_write"],
            }
            for model_id, m in models.items()
            if "cost" in m
        },
    }


def main() -> int:
    try:
        catalog = fetch()
    except OSError as exc:
        print(f"could not fetch {SOURCE}: {exc}", file=sys.stderr)
        return 1
    priced = build(catalog)
    OUT.write_text(json.dumps(priced, indent=2) + "\n")
    print(f"wrote {len(priced['models'])} models to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
