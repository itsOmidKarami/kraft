"""`dev/refresh_prices.py` builds `prices.json` for every provider the shipped
harnesses launch, deriving the cache-write rate for Anthropic only."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "refresh_prices.py"
_spec = importlib.util.spec_from_file_location("_dev_refresh_prices", _SCRIPT)
refresh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(refresh)


def test_build_prices_every_provider_deriving_cache_write_for_anthropic_only():
    cost = {"input": 2, "output": 10, "cache_read": 0.2, "cache_write": 2.5}
    long = {"input": 4, "output": 15, "tier": {"type": "context", "size": 272000}}
    catalog = {
        "anthropic": {"models": {"claude-x": {"cost": cost}}},
        "openai": {
            "models": {
                "gpt-x": {"cost": cost},
                "gpt-long": {"cost": {**cost, "tiers": [long]}},
                "no-price": {},
            }
        },
        "google": {"models": {"gemini-x": {"cost": {"input": 1, "output": 3, "cache_read": 0.1}}}},
    }
    models = refresh.build(catalog)["models"]
    assert models == {
        "claude-x": {"input": 2, "output": 10, "cache_read": 0.2, "cache_write": 4},
        "gpt-x": cost,
        "gemini-x": {"input": 1, "output": 3, "cache_read": 0.1, "cache_write": 0},
        # Kraft-tugdf.14: a long-context tier, its missing rates filled the
        # base tier's way.
        "gpt-long": {
            **cost,
            "tiers": [
                {"above": 272000, "input": 4, "output": 15, "cache_read": 4, "cache_write": 0}
            ],
        },
    }
