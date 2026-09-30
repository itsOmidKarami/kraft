"""`dev/refresh_prices.py` builds `prices.json` for both providers the shipped
harnesses launch, deriving the cache-write rate for Anthropic only."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "refresh_prices.py"
_spec = importlib.util.spec_from_file_location("_dev_refresh_prices", _SCRIPT)
refresh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(refresh)


def test_build_prices_both_providers_deriving_cache_write_for_anthropic_only():
    cost = {"input": 2, "output": 10, "cache_read": 0.2, "cache_write": 2.5}
    catalog = {
        "anthropic": {"models": {"claude-x": {"cost": cost}}},
        "openai": {"models": {"gpt-x": {"cost": cost}, "no-price": {}}},
    }
    models = refresh.build(catalog)["models"]
    assert models == {
        "claude-x": {"input": 2, "output": 10, "cache_read": 0.2, "cache_write": 4},
        "gpt-x": cost,
    }
