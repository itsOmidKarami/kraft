"""`kind: kit` against a real container runtime, docker and podman: a Kit
pushed to a registry on loopback (`support.registry`), fetched through the
runtime's own CLI, lowered, and launched from its digest by `run_task`
(`sandbox-kit-runs-only-what-kraft-enforces`).

The transports, the seam that sends `allowed.test` to a host server and the
fake API behind it are `test_egress_docker.py`'s and
`test_credentials_docker.py`'s. The CLI must trust the registry's own
certificate for the fetch: docker's through `SSL_CERT_FILE`, which Go reads
on Linux only, and podman's through `CONTAINERS_REGISTRIES_CONF`, which a
podman machine's service never sees. So on macOS these skip naming why;
CI's Linux runner runs them:

    KRAFT_E2E_REQUIRE=docker,podman just test tests/worker/test_kit_docker.py --no-testmon
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from support import registry
from support.harness import entry_of

from kraft.paths import default_templates_dir
from kraft.policy import DEFAULT_SENTINEL
from kraft.worker import kit

# Shared, not copied: the runtime under test, over each transport, and the
# fake API that answers only the real key.
from worker.test_credentials_docker import IMAGE, REAL, api  # noqa: F401
from worker.test_egress_docker import (  # noqa: F401
    _pulled,
    _unavailable,
    launch,
    probed,
    runtime,
    short_run,
)

#: The Kit: allowed.test at runtime, its key injected there. `required.test`
#: stands for a host the harness requires and the Kit leaves out.
DESCRIPTOR = json.dumps(
    {
        "schemaVersion": "3",
        "kind": "workload",
        "capabilities": [
            {
                "type": kit.NETWORK,
                "config": {"runtime": {"allow": ["allowed.test"]}},
            },
            {
                "type": kit.CREDENTIAL,
                "config": {
                    "service": "e2e",
                    "phase": "runtime",
                    "apiKey": {
                        "name": "E2E_API_KEY",
                        "proxyManaged": True,
                        "inject": [{"domain": "allowed.test", "header": "x-api-key"}],
                    },
                },
            },
        ],
    }
)

# The real key, spelled so it is never whole in the script's own argv:
# (a) what the container holds and whether any environ has the key; (b) the
# allowed host, carrying the sentinel; (c) the harness's host, not the Kit's.
_SCRIPT = """
K=sk-real-""e2e-7f3a9c
echo "env=$E2E_API_KEY" > out
grep -lF "$K" /proc/*/environ >> out 2>/dev/null
curl -sS -m 30 -H "x-api-key: $E2E_API_KEY" https://allowed.test/v1 >> out 2>&1
echo " rc=$?" >> out
curl -sS -m 30 -o /dev/null -w "required=%{{http_code}}" http://required.test/ >> out 2>&1
echo " rc=$?" >> out
"""


@pytest.fixture
def kit_ref(probed, monkeypatch):  # noqa: F811
    """The Kit on a registry the runtime's CLI trusts, bound to the real key
    in the daemon's environment; skipped where the CLI cannot be made to
    trust it without touching the machine."""
    cli = probed.cli
    _pulled(cli, IMAGE)
    _pulled(cli, registry.IMAGE)
    certs = Path(tempfile.mkdtemp(prefix="kraft-e2e-registry-"))
    served = registry.start(cli, certs)
    try:
        ref = served.kit(IMAGE, DESCRIPTOR)
        conf = certs / "registries.conf"
        conf.write_text(f'[[registry]]\nlocation = "{served.host}"\ninsecure = true\n')
        monkeypatch.setenv("CONTAINERS_REGISTRIES_CONF", str(conf))
        monkeypatch.setenv("SSL_CERT_FILE", str(served.cert))
        monkeypatch.setenv("KRAFT_E2E_KIT_KEY", REAL)
        templates = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
        sandbox_yaml = templates / "sandbox.yaml"
        sandbox_yaml.write_text(
            f"{sandbox_yaml.read_text()}credentials: {{e2e: KRAFT_E2E_KIT_KEY}}\n"
        )
        yield ref
    finally:
        served.stop()
        shutil.rmtree(certs, ignore_errors=True)


async def test_a_kit_runs_from_its_digest_with_its_surface_alone(
    launch,  # noqa: F811
    runtime,  # noqa: F811
    api,  # noqa: F811
    kit_ref,
):
    """Fetched through the CLI (podman reads a single manifest from the
    pulled image), the launch runs the Kit's image by digest: (a) its
    credential only as the sentinel, (b) the allowed host reached with the
    real key injected, (c) the host the harness requires and the Kit does
    not refused (403) and recorded."""
    try:
        fetched = await kit.ensure(kit_ref)
    except kit.KitRefused as exc:
        _unavailable(runtime.cli, f"e2e: {runtime.cli} cannot fetch the test Kit here: {exc}")
    # Outside the try: a lowering bug fails, never skips.
    lowered = kit.lower(kit_ref, fetched.descriptor(), kit.bindings())
    port, seen = await api("allowed.test", "x-api-key")
    sandbox = {**lowered.policy.model_dump(), "kit": kit_ref}
    assert (sandbox["image"], fetched.manifest) == (kit_ref, kit_ref.rpartition("@")[2])

    out, refused, *_ = await launch(
        _SCRIPT,
        {},
        sandbox=sandbox,
        upstream=("allowed.test", port),
        repo_entry=entry_of({"path": "/r"}),
        network_requires=("required.test",),
    )

    assert out.splitlines() == [
        f"env={DEFAULT_SENTINEL}",  # (a): and no environ holds the key
        "api-answered rc=0",  # (b)
        "required=403 rc=0",  # (c)
    ], out
    assert seen == [REAL], seen
    # By the lists, not a lookup that failed: with the harness's hosts added,
    # required.test would be allowed and only then fail to resolve.
    assert [(r["host"], r["reason"]) for r in refused] == [
        ("required.test", "required.test:80 is not on the allow list")
    ], refused
