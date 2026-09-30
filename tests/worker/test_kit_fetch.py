"""`kraft.worker.kit`'s fetch and cache, against `fake_docker_bin`'s
`manifest inspect`."""

import json
import os

import pytest
from support.fake_docker import fake_docker_bin

from kraft.worker import kit
from kraft.worker.backends import docker

DESCRIPTOR = '{"schemaVersion":"3","kind":"workload"}'
REPO = "registry.example.com/acme/kit"
REF = f"{REPO}:1.0@sha256:" + "a" * 64
PLATFORM_DIGEST = "sha256:" + "b" * 64
ATTESTATION_DIGEST = "sha256:" + "c" * 64
ANNOTATED = {kit.ANNOTATION: DESCRIPTOR}
ATTESTATION = {
    "digest": ATTESTATION_DIGEST,
    "platform": {"os": "unknown", "architecture": "unknown"},
}
PLATFORM = {"digest": PLATFORM_DIGEST, "platform": {"os": "linux", "architecture": "arm64"}}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Put a manifest `manifest inspect REF` answers with: `registry(ref, doc)`."""
    monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}:{os.environ['PATH']}")
    answers = tmp_path / "manifests"
    answers.mkdir()
    monkeypatch.setenv("FAKE_DOCKER_MANIFESTS", str(answers))

    def put(ref: str, manifest: dict) -> None:
        name = ref.translate(str.maketrans("/:@", "___"))
        (answers / f"{name}.json").write_text(json.dumps(manifest))

    # The attestation answers with the annotation too, so reading it instead
    # of a platform manifest shows in the digest, not as a refusal.
    put(f"{REPO}:1.0@{ATTESTATION_DIGEST}", {"annotations": ANNOTATED})
    return put


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
    """docker 29 drops an index's annotations, so a platform manifest is
    read by digest (SPEC §9.3's mandatory fallback): the first that is not
    an attestation."""
    if case == "index":
        registry(REF, {"manifests": [ATTESTATION, PLATFORM], "annotations": ANNOTATED})
    elif case == "platform-fallback":
        registry(REF, {"manifests": [ATTESTATION, PLATFORM]})
        registry(f"{REPO}:1.0@{PLATFORM_DIGEST}", {"annotations": ANNOTATED})
    else:
        registry(REF, {"layers": [], "annotations": ANNOTATED})
    fetched = await kit.fetch(REF)
    assert (fetched.manifest, fetched.descriptor().kind) == (
        manifest.rpartition("@")[2],
        "workload",
    )


@pytest.mark.parametrize(
    ("ref", "manifest", "refusal"),
    [
        (REF, {"layers": [], "annotations": {"x": "y"}}, f"is not a Kit: no {kit.ANNOTATION}"),
        (REF, {"manifests": [ATTESTATION]}, f"is not a Kit: no {kit.ANNOTATION}"),
        (f"{REPO}:1.0", {"annotations": ANNOTATED}, "is not pinned"),
    ],
    ids=["no-annotation", "attestations-only", "unpinned"],
)
async def test_a_reference_that_is_not_a_kit_is_refused(registry, ref, manifest, refusal):
    registry(ref, manifest)
    with pytest.raises(kit.KitRefused, match=refusal):
        await kit.fetch(ref)


@pytest.mark.parametrize("case", ["stderr", "no-answer", "oversized"])
async def test_a_cli_that_fails_is_a_refusal_quoting_it(registry, monkeypatch, case):
    refusal = {
        "stderr": f"failed: manifest unknown: {REF}",
        "no-answer": "did not answer",
        "oversized": "answered over 64 bytes",
    }[case]
    if case == "no-answer":
        monkeypatch.setenv("FAKE_DOCKER_HANG", "1")
        monkeypatch.setattr(docker, "DOCKER_CALL_TIMEOUT_S", 0.5)
    if case == "oversized":
        # Still running when the limit kills it, as a slow client would be:
        # its exit code is then the kill's, never 0.
        monkeypatch.setenv("FAKE_DOCKER_LINGER", "1")
        monkeypatch.setattr(kit, "MAX_MANIFEST", 64)
        registry(REF, {"layers": [], "annotations": ANNOTATED})
    with pytest.raises(kit.KitRefused, match=f"^`manifest inspect {REF}` {refusal}"):
        await kit.fetch(REF)


@pytest.mark.parametrize(
    "stored", [None, "not json", '{"manifest": 1}'], ids=["none", "garbage", "wrong-shape"]
)
async def test_a_cached_kit_is_not_fetched_again(registry, tmp_path, monkeypatch, stored):
    """Keyed by the pinned digest, so never stale and fetched once. An
    entry that does not read is a miss, fetched again, never kept for good."""
    calls = tmp_path / "calls"
    monkeypatch.setenv("FAKE_DOCKER_CALLS", str(calls))
    registry(REF, {"manifests": [PLATFORM]})
    registry(f"{REPO}:1.0@{PLATFORM_DIGEST}", {"annotations": ANNOTATED})
    if stored is not None:
        kit._cache(REF).parent.mkdir(parents=True)
        kit._cache(REF).write_text(stored)
    assert kit.cached(REF) is None
    first = await kit.ensure(REF)
    assert (await kit.ensure(REF), kit.cached(REF)) == (first, first)
    assert len(calls.read_text().splitlines()) == 2  # the index and its platform manifest
