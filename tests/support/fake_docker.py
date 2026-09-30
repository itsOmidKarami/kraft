"""A fake `docker` CLI for the unit tier (`fake_docker_bin`)."""

from __future__ import annotations

from pathlib import Path


def fake_docker_bin(tmp_path: Path) -> Path:
    """A directory holding a `docker` that unwraps `docker run [OPTIONS] IMAGE
    CMD...` back to `CMD...` and execs it in its `-w` — proves the wrap shape a
    real sandbox launch produces without a real daemon. `-u`/`-v`/`-w`/`-e`/
    `--name`/`--label` each take one following argument, so skipping
    flag+value pairs finds the image (the first survivor) and the command
    (everything after it). `--init` and `--flag=value`s are dropped. `docker
    info` answers nothing: a rootful runtime. `--cidfile=PATH` gets a fake
    container id, as docker writes one once it has created the container.
    `docker ps` prints `$FAKE_DOCKER_PS` (container names) when set; `docker
    rm -f NAME` -- `docker.teardown` -- appends `NAME` to `$FAKE_DOCKER_RM_LOG`
    when set, and deletes `$FAKE_DOCKER_INSPECT`, which `docker inspect`
    prints until then (`true 33554432`: a 32m limit OOM-killed it).
    `docker manifest inspect REF` prints `$FAKE_DOCKER_MANIFESTS/<REF with
    / : @ as _>.json`, or fails on stderr when there is none, or hangs when
    `$FAKE_DOCKER_HANG` is set. Every call appends its arguments to
    `$FAKE_DOCKER_CALLS` when set.
    """
    bin_dir = tmp_path / "fake-docker-bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        '# Test seam: touch a sentinel if asked, so a test can tell "the real\n'
        '# command ran because it went through this fake docker" apart from\n'
        '# "the real command ran because nothing wrapped it at all" -- the two\n'
        "# look identical from the marker file the real command itself writes.\n"
        'if [ -n "${FAKE_DOCKER_CALLED:-}" ]; then : > "$FAKE_DOCKER_CALLED"; fi\n'
        'if [ -n "${FAKE_DOCKER_CALLS:-}" ]; then echo "$*" >> "$FAKE_DOCKER_CALLS"; fi\n'
        'if [ "$1" = manifest ]; then\n'
        '  [ -z "${FAKE_DOCKER_HANG:-}" ] || exec sleep 60\n'
        '  answer="${FAKE_DOCKER_MANIFESTS:-/nonexistent}/$(printf %s "$3" | tr "/:@" ___).json"\n'
        '  [ -f "$answer" ] || { echo "manifest unknown: $3" >&2; exit 1; }\n'
        '  cat "$answer"; exit 0\n'
        "fi\n"
        '[ "$1" = ps ] && { [ -z "${FAKE_DOCKER_PS:-}" ] || cat "$FAKE_DOCKER_PS"; exit 0; }\n'
        '[ "$1" = info ] && exit 0\n'
        '[ "$1" = inspect ] && { cat "${FAKE_DOCKER_INSPECT:-/nonexistent}"; exit; }\n'
        'if [ "$1" = "rm" ]; then\n'
        "  shift\n"
        '  [ -z "${FAKE_DOCKER_INSPECT:-}" ] || rm -f "$FAKE_DOCKER_INSPECT"\n'
        '  for arg in "$@"; do\n'
        '    [ "$arg" = "-f" ] && continue\n'
        '    if [ -n "${FAKE_DOCKER_RM_LOG:-}" ]; then echo "$arg" >> "$FAKE_DOCKER_RM_LOG"; fi\n'
        "  done\n"
        "  exit 0\n"
        "fi\n"
        'shift # drop "run"\n'
        'image=""\n'
        "cmd=()\n"
        "skip=0\n"
        'for arg in "$@"; do\n'
        '  if [ "$skip" != 0 ]; then [ "$skip" != -w ] || cd "$arg"; skip=0; continue; fi\n'
        '  case "$arg" in\n'
        '    --cidfile=*) printf fake-container-id > "${arg#--cidfile=}"; continue ;;\n'
        '    --rm|--init|--*=*) [ -n "$image" ] && cmd+=("$arg"); continue ;;\n'
        "    -u|-v|-w|-e|--name|--label) skip=$arg; continue ;;\n"
        "    *)\n"
        '      if [ -z "$image" ]; then image="$arg"; else cmd+=("$arg"); fi\n'
        "      ;;\n"
        "  esac\n"
        "done\n"
        'exec "${cmd[@]}"\n'
    )
    docker.chmod(0o755)
    return bin_dir
