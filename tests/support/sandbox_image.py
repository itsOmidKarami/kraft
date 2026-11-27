"""A git image built with a real container runtime, for the e2e tests that
run a sandboxed worker in docker or podman."""

import os
import subprocess
from pathlib import Path

import pytest

IMAGE = "kraft-test-git:latest"

# pytest-timeout (120s, `thread`) kills the whole xdist worker with os._exit,
# fixture setup included, so a pull throttled by a registry (podman retries it
# three times, ~30s apiece) would take every later test on that worker with it.
# A subprocess limit under the test's own turns that into one ordinary failure.
# A fixture may pull twice, so two of these still fit in 120s; the slowest
# healthy setup in CI is ~21s.
PULL_TIMEOUT_S = 40
# One build of the git image: base pull plus an optional `apk add`, healthy in seconds.
BUILD_TIMEOUT_S = 80


def pull_failure(cli: str, image: str, *args: str) -> str | None:
    """Why `cli pull image` failed, or None once it succeeded. A pull past
    PULL_TIMEOUT_S fails naming the image rather than outliving the test."""
    try:
        pulled = subprocess.run(
            [cli, "pull", "-q", *args, image],
            capture_output=True,
            text=True,
            timeout=PULL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return f"`{cli} pull {image}` took over {PULL_TIMEOUT_S}s (a throttled registry?)"
    return None if pulled.returncode == 0 else pulled.stderr.strip()


def build_git_image(
    cli: str, tmp_path: Path, monkeypatch, *, curl: bool = False, python: bool = False
) -> str:
    """`alpine/git` with its `git` entrypoint cleared, so a command runs as
    itself (and `curl`, which the `kraft` shim needs, or `python3`, which a
    Python agent needs, when asked), built with
    `cli` and made this machine's runtime through
    `sandbox.yaml`, rootless or not as it really is. Skips where the runtime
    cannot build it, unless KRAFT_E2E_REQUIRE names it."""
    from kraft.paths import config_dir
    from kraft.worker.backends import docker

    context = tmp_path / "image"
    context.mkdir()
    extras = [name for name, wanted in (("curl", curl), ("python", python)) if wanted]
    image = "-".join([IMAGE.split(":")[0], *extras]) + ":latest" if extras else IMAGE
    (context / "Dockerfile").write_text(
        "FROM docker.io/alpine/git:latest\n"
        + ("RUN apk add --no-cache curl\n" if curl else "")
        + ("RUN apk add --no-cache python3\n" if python else "")
        + "ENTRYPOINT []\n"
    )
    try:
        built = subprocess.run(
            [cli, "build", "-q", "-t", image, str(context)],
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT_S,
        )
        failed = built.stderr.strip() if built.returncode != 0 else None
    except subprocess.TimeoutExpired:
        failed = f"took over {BUILD_TIMEOUT_S}s (a throttled registry?)"
    if failed is not None:
        why = f"e2e: {cli} could not build {image}: {failed}"
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
    return image
