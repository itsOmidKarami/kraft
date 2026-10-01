"""Refreshes `src/kraft/prices.json` from models.dev's Anthropic, OpenAI and Google
listings -- the providers the shipped harnesses launch (claude; codex; gemini
and antigravity).

Not imported at runtime (`usage._prices` reads the committed JSON file, never
the network -- see that function's docstring): this is a one-shot script a
human runs (`just refresh-prices`) when a provider's pricing changes, the same
"generate, paste/commit, test the committed output" shape as `dev/gen_palette.py`.
"""

from __future__ import annotations

import datetime
import json
import sys
import urllib.request
from pathlib import Path

SOURCE = "https://models.dev/api.json"
#: Each provider's models go in under their own ids, which are the ids a
#: harness is launched with (`harnesses.yaml`: codex's `gpt-5.6-sol`).
PROVIDERS = ("anthropic", "openai", "google")
OUT = Path(__file__).resolve().parents[1] / "src" / "kraft" / "prices.json"


def fetch(url: str = SOURCE) -> dict:
    # models.dev answers urllib's default User-Agent with a 403.
    req = urllib.request.Request(url, headers={"User-Agent": "kraft-refresh-prices"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 -- a fixed, known URL
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


def _flat(provider: str, cost: dict) -> dict:
    """One price tier's rates. Anthropic's cache write is derived (see
    `CACHE_WRITE_MULTIPLE`); every other provider's are models.dev's own, a
    missing cache-read rate billed as plain input and a missing cache-write
    rate as none."""
    if provider == "anthropic":
        cache_write = cost["input"] * CACHE_WRITE_MULTIPLE
    else:
        cache_write = cost.get("cache_write", 0)
    return {
        "input": cost["input"],
        "output": cost["output"],
        "cache_read": cost.get("cache_read", cost["input"]),
        "cache_write": cache_write,
    }


def _rates(provider: str, cost: dict) -> dict:
    """One model's base rates, and its long-context tiers (models.dev's
    `tiers` of type `context`, e.g. OpenAI's past 272k) under `tiers`, each
    with the context size it applies `above`, lowest first
    (`usage.estimate_cost`)."""
    tiers = [
        {"above": t["tier"]["size"], **_flat(provider, t)}
        for t in cost.get("tiers", ())
        if t.get("tier", {}).get("type") == "context"
    ]
    rates = _flat(provider, cost)
    return {**rates, "tiers": sorted(tiers, key=lambda t: t["above"])} if tiers else rates


def build(catalog: dict, providers: tuple[str, ...] = PROVIDERS) -> dict:
    """`catalog` is models.dev's whole `api.json`; the result is the shape
    `prices.json` stores: per-model USD-per-million-token rates for the
    providers' models, plus where and when they were read."""
    return {
        "source": SOURCE,
        "providers": list(providers),
        "snapshot_date": datetime.date.today().isoformat(),
        "note": (
            "USD per million tokens. cache_read is models.dev's own cache-hit "
            "rate. For anthropic models cache_write is NOT models.dev's own "
            "figure -- that's Anthropic's 5-minute cache-creation tier (1.25x "
            "input); Claude Code writes 1-hour entries, billed at 2x input (see "
            "CACHE_WRITE_MULTIPLE in dev/refresh_prices.py for the derivation "
            "and the proof from a real session's total_cost_usd). Other "
            "providers' rates are models.dev's own. tiers: the rates a session "
            "is billed at once one request's context passes `above` tokens. Refresh "
            "with: just refresh-prices (dev/refresh_prices.py)."
        ),
        "models": {
            model_id: _rates(provider, m["cost"])
            for provider in providers
            for model_id, m in catalog[provider]["models"].items()
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
