from __future__ import annotations

import builtins
import os
import pwd
import sys
from pathlib import Path

import pytest

from kraft.index.embed import DIMENSIONS, MODEL_NAME, Embedder, cache_dir


def test_no_input_never_loads_a_model():
    e = Embedder()
    # would raise if it tried to import or load anything
    e._load = lambda: (_ for _ in ()).throw(AssertionError("model must not load"))
    assert e.encode([]) == []


_RUNNING = f"{sys.version_info.major}.{sys.version_info.minor}"


@pytest.mark.parametrize(
    ("kind", "receipt", "hint"),
    [
        (
            "uv",
            'python = "3.12"\n',
            '`uv tool install --force --python 3.12 "kraft-sdlc[vector]==2.0.0rc1"`',
        ),
        (
            "uv",
            "",
            f'`uv tool install --force --python {_RUNNING} "kraft-sdlc[vector]==2.0.0rc1"`',
        ),
        ("pipx", None, '`pipx install --force --python /py/3 "kraft-sdlc[vector]==2.0.0rc1"`'),
        ("pip", None, ' --upgrade "kraft-sdlc[vector]==2.0.0rc1"`'),
        ("brew", None, "Homebrew's kraft formula does not ship the 'vector' extra"),
        ("source", None, "`uv sync --extra vector` in the checkout"),
    ],
    ids=["uv-chosen-python", "uv-running-python", "pipx", "pip-venv", "homebrew", "source"],
)
def test_unavailable_when_the_extra_is_missing(tmp_path, monkeypatch, kind, receipt, hint):
    """R11c-01: the hint named no version and no Python. On a release
    candidate it installed the last final release, which refused to start on
    the newer database, on whatever Python uv chose; on pip, pipx and
    Homebrew it added a second Kraft. It reinstalls this version, on this
    Python, the way this Kraft was installed."""
    import importlib.metadata
    import importlib.util

    from kraft import update

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "fastembed":
            raise ImportError("no fastembed")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(update, "installed", lambda: "2.0.0rc1")
    if kind != "source":
        prefix = tmp_path / "venv"
        if kind == "brew":
            prefix = tmp_path / "Cellar" / "kraft" / "2.0.0rc1" / "libexec"
        prefix.mkdir(parents=True)
        marker = {"uv": "uv-receipt.toml", "pipx": "pipx_metadata.json"}.get(kind)
        if marker:
            (prefix / marker).write_text(f"[tool]\n{receipt}" if kind == "uv" else "{}")
        monkeypatch.setattr(update.sys, "prefix", str(prefix))
        monkeypatch.setattr(update.sys, "_base_executable", "/py/3", raising=False)
        monkeypatch.setattr(
            importlib.metadata,
            "distribution",
            lambda name: type("D", (), {"read_text": lambda self, f: None})(),
        )
        monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None)

    e = Embedder()
    assert e.available() is False
    assert e.reason.startswith("the 'vector' extra is not installed — ")
    assert hint in e.reason


def test_encode_raises_when_unavailable(monkeypatch):
    e = Embedder()
    monkeypatch.setattr(e, "available", lambda: False)
    with pytest.raises(RuntimeError):
        e.encode(["hello"])


def test_try_encode_swallows_and_records(monkeypatch):
    e = Embedder()
    monkeypatch.setattr(e, "available", lambda: True)
    monkeypatch.setattr(e, "_load", lambda: (_ for _ in ()).throw(OSError("download failed")))
    assert e.try_encode(["hello"]) is None
    assert "download failed" in (e.reason or "")


def test_the_last_encode_failure_is_kept_until_a_later_success(monkeypatch):
    """Kraft-pm2rj: health reads `reason` to say a configured embedder is
    broken, so a model that fails after it loaded is recorded, and a later
    success clears it rather than leave a stale FAIL."""
    outcomes = [RuntimeError("onnx session died"), [[1.0]]]

    class _Model:
        def embed(self, texts):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    e = Embedder()
    monkeypatch.setattr(e, "available", lambda: True)
    e._model = _Model()
    assert e.try_encode(["hello"]) is None
    assert e.reason == "onnx session died"
    assert e.try_encode(["hello"]) == [[1.0]]
    assert e.reason is None


def test_available_is_true_with_the_extra_installed():
    pytest.importorskip("fastembed")
    assert Embedder().available() is True


@pytest.fixture
def machine_model_cache(monkeypatch):
    """This machine's model cache. `cache_dir()` derives it from `HOME`, which
    tests/conftest.py points at an empty temp dir, so weights already on disk
    were never found and the two tests below always skipped. Neither writes
    a download into it: each skips when the weights are not there."""
    if not os.environ.get("KRAFT_EMBED_CACHE"):
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        base = Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache")
        monkeypatch.setenv("KRAFT_EMBED_CACHE", str(base / "kraft" / "fastembed"))
    return cache_dir()


def test_the_model_cache_is_looked_for_under_the_real_home(machine_model_cache):
    assert not machine_model_cache.is_relative_to(os.environ["HOME"])
    assert machine_model_cache.parts[-2:] == ("kraft", "fastembed")


def _model_is_cached() -> bool:
    """True when the weights are already on disk, so no network is needed."""
    root = cache_dir()
    if not root.is_dir():
        return False
    wanted = MODEL_NAME.split("/")[-1].lower()
    return any(wanted in p.name.lower() for p in root.rglob("*"))


@pytest.mark.slow
def test_real_embedding_has_the_declared_width_and_ranks_sensibly(machine_model_cache):
    pytest.importorskip("fastembed")
    if not _model_is_cached():
        pytest.skip("model not cached; refusing to download in the test suite")
    e = Embedder()
    vecs = e.encode(
        [
            "the websocket reconnect backoff schedule",
            "websocket reconnection retry timing",
            "sourdough bread starter hydration",
        ]
    )
    assert len(vecs) == 3
    assert all(len(v) == DIMENSIONS for v in vecs)

    def cos(a, b):
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb)

    assert cos(vecs[0], vecs[1]) > cos(vecs[0], vecs[2])


@pytest.mark.slow
def test_encode_async_matches_sync(machine_model_cache):
    import asyncio

    pytest.importorskip("fastembed")
    if not _model_is_cached():
        pytest.skip("model not cached; refusing to download in the test suite")
    e = Embedder()
    assert asyncio.run(e.encode_async([])) == []
    got = asyncio.run(e.encode_async(["hello"]))
    assert len(got) == 1 and len(got[0]) == DIMENSIONS
