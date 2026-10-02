"""A proxy-managed credential against a real container runtime, docker and
podman (`sandbox-credentials-never-enter-the-container`): the container
holds the sentinel and nothing else, and the egress proxy puts the real key
in the header on its way to the host, for a request carrying the sentinel
and no other.

The host is a fake API on this machine, over TLS with a certificate from a
test CA the daemon trusts through `sandbox.yaml` `ca_bundle`; it answers 200
only for the real key. The transports, the seam that sends `allowed.test`
to it and the manual run are `test_egress_docker.py`'s:

    KRAFT_E2E_REQUIRE=docker,podman just test tests/worker/test_credentials_docker.py --no-testmon
"""

import asyncio
import os
import shlex
import shutil
import ssl
import subprocess
import tempfile
from pathlib import Path

import pytest
from support.harness import entry_of

from kraft import harness
from kraft.paths import RunDirs, default_templates_dir
from kraft.policy import SandboxCredential
from kraft.worker import ca

# Shared, not copied: the runtime under test, over each transport.
from worker.test_egress_docker import (  # noqa: F401
    _pulled,
    launch,
    probed,
    runtime,
    short_run,
)

IMAGE = "docker.io/curlimages/curl:latest"
REAL = "sk-real-e2e-7f3a9c"
SENTINEL = "sk-sentinel-e2e"
CREDENTIAL = SandboxCredential.model_validate(
    {
        "env": "E2E_API_KEY",
        "sentinel": SENTINEL,
        "inject": [{"domain": "allowed.test", "header": "x-api-key"}],
    }
)

# The real key, spelled so that it is never whole in the script's own argv.
# (a) every /proc/*/environ, then every mount but the kernel's, for the key;
# (b) the sentinel, as the proxy expects it; (c) anything else.
_SCRIPT = """
K=sk-real-""e2e-7f3a9c
echo "env=$E2E_API_KEY" > out
grep -lF "$K" /proc/*/environ >> out 2>/dev/null
for m in $(awk '$5 != "/" && $5 !~ "^/(proc|sys|dev)" {{print $5}}' /proc/self/mountinfo); do
  echo "mount $m" >> out; grep -rlF "$K" "$m" >> out 2>/dev/null
done
curl -sS -m 30 -H "x-api-key: $E2E_API_KEY" https://allowed.test/v1 >> out 2>&1
echo " rc=$?" >> out
curl -sS -m 30 -o /dev/null -w "wrong=%{{http_code}}" -H "x-api-key: wrong" \
  https://allowed.test/v1 >> out 2>&1
echo " rc=$?" >> out
"""


@pytest.fixture
async def api(probed):  # noqa: F811
    """`api(host, header)`: a fake API for `host` on this machine, answering
    200 only when `header` is the real key: its port, and `header`'s value on
    every request it saw. A test CA signs it, and the daemon trusts that CA
    through `sandbox.yaml`, never the Kraft CA."""
    test_ca = RunDirs(Path(tempfile.mkdtemp(prefix="kraft-e2e-ca-"))).ensure()
    templates = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    sandbox_yaml = templates / "sandbox.yaml"
    ca_bundle = ca.ensure_ca(test_ca)[0]
    sandbox_yaml.write_text(f"{sandbox_yaml.read_text()}ca_bundle: {ca_bundle}\n")
    servers = []

    async def start(host: str, header: str, real: str = REAL):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(*ca.mint_host_leaf(test_ca, host))
        seen: list[str] = []

        async def serve(reader, writer):
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
            values = [
                line.split(":", 1)[1].strip()
                for line in head.split("\r\n")
                if line.lower().startswith(f"{header}:")
            ]
            seen.append(values[0] if len(values) == 1 else f"{len(values)} values")
            ok = seen[-1] == real
            body = b"api-answered" if ok else b"wrong-key"
            status = b"200 OK" if ok else b"401 Unauthorized"
            writer.write(
                b"HTTP/1.1 "
                + status
                + b"\r\nContent-Length: %d\r\n" % len(body)
                + b"Connection: close\r\n\r\n"
                + body
            )
            await writer.drain()
            writer.close()

        servers.append(await asyncio.start_server(serve, "127.0.0.1", 0, ssl=context))
        return servers[-1].sockets[0].getsockname()[1], seen

    yield start
    for server in servers:
        server.close()
    shutil.rmtree(test_ca.base, ignore_errors=True)


async def test_a_managed_key_reaches_only_the_host_and_never_the_container(
    launch,  # noqa: F811
    runtime,  # noqa: F811
    api,
):
    """(a) The key is in no process's environ and on no mount, where the
    container holds the sentinel; (b) a request carrying the sentinel
    reaches the host with the real key, which it saw exactly once; (c) one
    carrying anything else is refused (403) and the host never sees it."""
    _pulled(runtime.cli, IMAGE)
    port, seen = await api("allowed.test", "x-api-key")
    entry = entry_of({"path": "/r", "env": {"E2E_API_KEY": REAL}})

    out, refused, *_ = await launch(
        _SCRIPT,
        {"runtime": {"allow": ["allowed.test"]}},
        image=IMAGE,
        upstream=("allowed.test", port),
        credentials=(CREDENTIAL,),
        repo_entry=entry,
    )

    lines = out.splitlines()
    assert lines[0] == f"env={SENTINEL}", out
    mounts = [line for line in lines[1:] if line.startswith("mount ")]
    assert mounts and lines[1 : 1 + len(mounts)] == mounts, out  # (a): no file named
    assert lines[1 + len(mounts) :] == ["api-answered rc=0", "wrong=403 rc=0"], out  # (b), (c)
    assert seen == [REAL], seen
    assert [r["host"] for r in refused] == ["allowed.test"], refused


#: The npm package each CLI ships as, installed into an image of its own.
_CLI_PACKAGES = {
    "claude": "@anthropic-ai/claude-code",
    "codex": "@openai/codex",
    "gemini": "@google/gemini-cli",
}


def _cli_image(cli: str, harness_id: str) -> str:
    """An image holding `harness_id`'s CLI, built once and kept."""
    tag = f"localhost/kraft-e2e-{harness_id}:latest"
    if subprocess.run([cli, "image", "inspect", tag], capture_output=True).returncode == 0:
        return tag
    containerfile = (
        "FROM docker.io/library/node:22-slim\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates"
        " curl && rm -rf /var/lib/apt/lists/*\n"
        f"RUN npm install -g {_CLI_PACKAGES[harness_id]}\n"
    )
    built = subprocess.run(
        [cli, "build", "-t", tag, "-"], input=containerfile, capture_output=True, text=True
    )
    assert built.returncode == 0, built.stderr[-2000:]
    return tag


# The CLI runs inside the image, not from this machine's PATH, and every request
# it makes goes to the fake API or is refused: no host CLI and no tokens, so
# `probed`'s `e2e("docker")`/`e2e("podman")` is all it needs.
@pytest.mark.parametrize("harness_id", list(_CLI_PACKAGES))
# An image not built yet is built here, once, well past the default timeout.
@pytest.mark.timeout(1800)
async def test_the_real_cli_sends_the_sentinel_and_trusts_the_bundle(
    launch,  # noqa: F811
    runtime,  # noqa: F811
    api,
    harness_id,
    tmp_path,
):
    """Task 6.5: each CLI whose harness declares a credential, run as a
    launch runs it, against a fake of its API host. The host getting the real
    key in the declared header is the proof: the CLI read the sentinel from
    its env, sent it there, and trusted the Kraft CA's leaf through the
    bundle, over HTTP/1.1. What the CLI makes of the fake's answer is not the
    question. A CLI that pins its host's certificate, or needs HTTP/2, fails
    here: its credential stays on passthrough, and the docs say so."""
    h = harness.load(None).valid[harness_id]
    declared = h.credentials[0]
    (rule,) = declared.inject
    port, seen = await api(rule.domain, rule.header, (rule.format or "%s").replace("%s", REAL))
    argv = harness.build_argv(h, prompt="Reply with OK.", context="A smoke test. Use no tools.")
    # A worktree is a git repository, which codex requires of its cwd.
    subprocess.run(["git", "init", "-q", str(tmp_path / "work")], check=True)
    script = shlex.join(argv).replace("{", "{{").replace("}", "}}") + " > out 2>&1"

    out, refused, *_ = await launch(
        script,
        {"runtime": {"allow": [rule.domain]}},
        image=_cli_image(runtime.cli, harness_id),
        upstream=(rule.domain, port),
        credentials=(SandboxCredential(env=declared.env),),
        declared=h.credentials,
        repo_entry=entry_of({"path": "/r", "env": {declared.env: REAL}}),
        network_requires=h.network_requires,
    )

    assert seen and set(seen) == {(rule.format or "%s").replace("%s", REAL)}, (out, refused)
