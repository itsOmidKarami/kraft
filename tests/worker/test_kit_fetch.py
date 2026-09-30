"""`kraft.worker.kit`'s fetch and cache, against `fake_docker_bin`'s
`manifest inspect`."""

import json
import os

import pytest
from support.harness import fake_docker_bin

from kraft.worker import kit
from kraft.worker.backends import docker

DESCRIPTOR = '{"schemaVersion":"3","kind":"workload"}'
REPO = "registry.example.com/acme/kit"
REF = f"{REPO}:1.0@sha256:" + "a" * 64
PLATFORM_DIGEST = "sha256:" + "b" * 64
ANNOTATED = {kit.ANNOTATION: DESCRIPTOR}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Put a manifest `manifest inspect REF` answers with: `registry(ref, doc)`."""
    monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}:{os.environ['PATH']}")
    answers = tmp_path / "manifests"
    answers.mkdir()
    monkeypatch.setenv("FAKE_DOCKER_MANIFESTS", str(answers))
    monkeypatch.setattr(kit, "_platform", lambda: ("linux", "arm64"))

    def put(ref: str, manifest: dict) -> None:
        name = ref.translate(str.maketrans("/:@", "___"))
        (answers / f"{name}.json").write_text(json.dumps(manifest))

    return put


def _index(*platforms: dict, annotations: dict | None = None) -> dict:
    manifests = [
        {
            "digest": PLATFORM_DIGEST if p.get("architecture") == "arm64" else "sha256:" + "c" * 64,
            "platform": p,
        }
        for p in platforms
    ]
    return {"manifests": manifests, **({"annotations": annotations} if annotations else {})}


@pytest.mark.parametrize(
    ("case", "manifest"),
    [
        ("index", REF),
        ("platform-fallback", PLATFORM_DIGEST),
        ("single-manifest", REF),
    ],
    ids=["index", "platform-fallback", "single-manifest"],
)
async def test_the_descriptor_comes_from_the_index_or_its_platform_manifest(
    registry, case, manifest
):
    """docker 29 drops an index's annotations, so the runtime's own
    platform manifest is read by digest (SPEC §9.3's mandatory fallback)."""
    amd64, arm64 = (
        {"os": "linux", "architecture": "amd64"},
        {"os": "linux", "architecture": "arm64"},
    )
    if case == "index":
        registry(REF, _index(amd64, arm64, annotations=ANNOTATED))
    elif case == "platform-fallback":
        registry(REF, _index(amd64, arm64))
        registry(f"{REPO}:1.0@{PLATFORM_DIGEST}", {"annotations": ANNOTATED})
    else:
        registry(REF, {"layers": [], "annotations": ANNOTATED})
    fetched = await kit.fetch(REF)
    assert (fetched.manifest, fetched.descriptor().kind) == (
        manifest.rpartition("@")[2],
        "workload",
    )


@pytest.mark.parametrize(
    "manifest",
    [
        {"layers": [], "annotations": {"org.opencontainers.image.title": "x"}},
        _index({"os": "unknown", "architecture": "unknown"}),
    ],
    ids=["no-annotation", "attestations-only"],
)
async def test_a_reference_that_is_not_a_kit_is_refused(registry, manifest):
    registry(REF, manifest)
    with pytest.raises(kit.KitRefused, match=f"is not a Kit: no {kit.ANNOTATION}"):
        await kit.fetch(REF)


@pytest.mark.parametrize(
    ("hang", "refusal"),
    [(False, f"failed: manifest unknown: {REF}"), (True, "did not answer")],
    ids=["stderr", "no-answer"],
)
async def test_a_cli_that_fails_is_a_refusal_quoting_it(registry, monkeypatch, hang, refusal):
    if hang:
        monkeypatch.setenv("FAKE_DOCKER_HANG", "1")
        monkeypatch.setattr(docker, "DOCKER_CALL_TIMEOUT_S", 0.5)
    with pytest.raises(kit.KitRefused, match=f"^`manifest inspect {REF}` {refusal}"):
        await kit.fetch(REF)


async def test_a_cached_kit_is_not_fetched_again(registry, tmp_path, monkeypatch):
    """Keyed by the pinned digest, so never stale and never fetched twice."""
    calls = tmp_path / "calls"
    monkeypatch.setenv("FAKE_DOCKER_CALLS", str(calls))
    registry(REF, _index({"os": "linux", "architecture": "arm64"}))
    registry(f"{REPO}:1.0@{PLATFORM_DIGEST}", {"annotations": ANNOTATED})
    assert kit.cached(REF) is None
    first = await kit.ensure(REF)
    assert (await kit.ensure(REF), kit.cached(REF)) == (first, first)
    assert len(calls.read_text().splitlines()) == 2  # the index and its platform manifest
