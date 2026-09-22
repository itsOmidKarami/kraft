from __future__ import annotations

import builtins

import pytest

from kraft.index.embed import DIMENSIONS, MODEL_NAME, Embedder, cache_dir


def test_no_input_never_loads_a_model():
    e = Embedder()
    # would raise if it tried to import or load anything
    e._load = lambda: (_ for _ in ()).throw(AssertionError("model must not load"))
    assert e.encode([]) == []


def test_unavailable_when_the_extra_is_missing(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "fastembed":
            raise ImportError("no fastembed")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    e = Embedder()
    assert e.available() is False
    assert "vector" in (e.reason or "")


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


def _model_is_cached() -> bool:
    """True when the weights are already on disk, so no network is needed."""
    root = cache_dir()
    if not root.is_dir():
        return False
    wanted = MODEL_NAME.split("/")[-1].lower()
    return any(wanted in p.name.lower() for p in root.rglob("*"))


@pytest.mark.slow
def test_real_embedding_has_the_declared_width_and_ranks_sensibly():
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
def test_encode_async_matches_sync():
    import asyncio

    pytest.importorskip("fastembed")
    if not _model_is_cached():
        pytest.skip("model not cached; refusing to download in the test suite")
    e = Embedder()
    assert asyncio.run(e.encode_async([])) == []
    got = asyncio.run(e.encode_async(["hello"]))
    assert len(got) == 1 and len(got[0]) == DIMENSIONS
