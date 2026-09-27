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


#: models.dev's own `cache_write` is Anthropic's 5-minute cache-creation tier
#: (1.25x input -- e.g. claude-sonnet-5's 2.5 against an input of 2). Claude
#: Code writes 1-hour cache entries, billed at 2x input, not 1.25x
#: (docs.anthropic.com/en/docs/build-with-claude/prompt-caching). models.dev
#: carries no separate 1h field to read instead (checked its whole catalog:
#: every provider's `cost` object has only one `cache_write` key). Proof, a
#: real finished claude-sonnet-5 session: tokens_in=22, cache_write=96191,
#: cache_read=1019087, output=7701, total_cost_usd=0.6656354 -- at 2x input
#: ($4/M) that's 0.000044 + 0.384764 + 0.203817 + 0.07701 = 0.6656354 exactly;
#: at the 5-minute $2.5/M it comes to 0.52, visibly short. So this multiplies
#: models.dev's `input` by this factor instead of trusting its `cache_write`.
CACHE_WRITE_MULTIPLE = 2


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
            "USD per million tokens. cache_read is models.dev's own cache-hit "
            "rate. cache_write is NOT models.dev's own figure -- that's "
            "Anthropic's 5-minute cache-creation tier (1.25x input); Claude Code "
            "writes 1-hour entries, billed at 2x input (see "
            "CACHE_WRITE_MULTIPLE in dev/refresh_prices.py for the derivation "
            "and the proof from a real session's total_cost_usd). Refresh with: "
            "just refresh-prices (dev/refresh_prices.py)."
        ),
        "models": {
            model_id: {
                "input": m["cost"]["input"],
                "output": m["cost"]["output"],
                "cache_read": m["cost"]["cache_read"],
                "cache_write": m["cost"]["input"] * CACHE_WRITE_MULTIPLE,
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
