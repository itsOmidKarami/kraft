"""A git image built with a real container runtime, for the e2e tests that
run a sandboxed worker in docker or podman."""

import os
import subprocess
from pathlib import Path

import pytest

IMAGE = "kraft-test-git:latest"


def build_git_image(cli: str, tmp_path: Path, monkeypatch) -> str:
    """`alpine/git` with its `git` entrypoint cleared, so a command runs as
    itself, built with `cli` and made this machine's runtime through
    `sandbox.yaml`, rootless or not as it really is. Skips where the runtime
    cannot build it, unless KRAFT_E2E_REQUIRE names it."""
    from kraft.paths import config_dir
    from kraft.worker.backends import docker

    context = tmp_path / "image"
    context.mkdir()
    (context / "Dockerfile").write_text("FROM docker.io/alpine/git:latest\nENTRYPOINT []\n")
    built = subprocess.run(
        [cli, "build", "-q", "-t", IMAGE, str(context)], capture_output=True, text=True
    )
    if built.returncode != 0:
        why = f"e2e: {cli} could not build {IMAGE}: {built.stderr.strip()}"
        # Where the runtime is required, an unreachable daemon or registry is
        # a failure, never a quiet skip past KRAFT_E2E_REQUIRE.
        if cli in os.environ.get("KRAFT_E2E_REQUIRE", "").split(","):
            pytest.fail(why)
        pytest.skip(why)
    if cli == "podman" and (host := os.environ.get("CONTAINER_HOST")):
        # A podman machine reached through CONTAINER_HOST (macOS): Kraft
        # launches `podman run` with the worker's allowlisted environment,
        # which drops it, and the test's own HOME holds no connection. Name it
        # there, in the one file that podman reads from HOME.
        # Only ever the suite's throwaway HOME, a sibling of `tmp_path` under
        # pytest's basetemp, and never over a file already there.
        home = Path(os.environ["HOME"]).resolve()
        if not home.is_relative_to(tmp_path.resolve().parent):
            pytest.fail(f"e2e: HOME {home} is not the suite's own; not writing podman config")
        conf = home / ".config" / "containers" / "containers.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        key = os.environ.get("CONTAINER_SSHKEY", "")
        with conf.open("x") as out:
            out.write(
                '[engine]\nactive_service = "e2e"\n[engine.service_destinations.e2e]\n'
                f'uri = "{host}"\nidentity = "{key}"\n'
            )
    templates = config_dir()
    templates.mkdir(parents=True, exist_ok=True)
    (templates / "sandbox.yaml").write_text(f"cli: {cli}\n")
    monkeypatch.setattr(docker, "_RUNTIME", docker.detect_runtime())
    return IMAGE
