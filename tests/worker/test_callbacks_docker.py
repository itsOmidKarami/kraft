"""A sandboxed worker's callbacks against a real container runtime, docker
and podman: `kraft` inside the container is the shim, and it reaches the
daemon only through its session's channel, as that session, on its own item
(`sandbox-callbacks-are-scoped-to-their-session`).

The daemon is the real app, its lifespan entered, so its own proxy, channel
registry and TLS listener serve the calls; the image is curl's (sh and curl
are all the shim needs). The transports and the manual run are
`test_egress_docker.py`'s, whose runtime fixtures this file shares:

    KRAFT_E2E_REQUIRE=docker,podman just test tests/worker/test_callbacks_docker.py --no-testmon
"""

import json
import os
import subprocess

import pytest
from support.api import _client, _force_node
from support.permissions import PATH, seed_session, templates

from kraft import events, store
from kraft.adapters import subprocess as sp
from kraft.worker import channel
from kraft.worker.egress import SANDBOX_EGRESS_REFUSED

# Shared, not copied: the runtime under test, over each transport.
from worker.test_egress_docker import _pulled, probed, runtime, short_run  # noqa: F401

IMAGE = "docker.io/curlimages/curl:latest"
PLAN = "# p\n\n## Task 1 — parse\n\n## Task 2 — serve\n"
_CODEX_BASH = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "/work"}


@pytest.fixture
def daemon(probed, runtime, short_run, monkeypatch):  # noqa: F811
    """`go(script)`: `script` run by `run_task` in a sandbox under
    `network:` on item `w1`, a session of its implementation task, whose
    policy denies Bash, while the daemon runs. Returns `(what the script
    wrote to ./out, the item's events)`; a launch that never ran fails with
    its log. Item `w2` has a review thread, whose id replaces `{other_thread}`
    in `script`."""
    _pulled(runtime.cli, IMAGE)
    tpl = templates(short_run.base, monkeypatch)
    (tpl / "sandbox.yaml").write_text(f"cli: {runtime.cli}\n")
    with _client(short_run.base, monkeypatch, templates_dir=tpl) as client:
        app = client.app
        seed_session(policy={"deny_tools": ["Bash"]})
        seed_session(sid="s2", wid="w2")
        _force_node("w1", "implementation", "active")
        run_dirs = app.state.run_dirs
        work = run_dirs.worktrees / "w1"
        (work / ".engineering" / "plans").mkdir(parents=True)
        (work / ".engineering" / "plans" / "w1.md").write_text(PLAN)
        subprocess.run(["git", "init", "-q"], cwd=work, check=True)

        async def thread():
            return await app.state.db.write(
                lambda c: store.create_thread(c, wid="w2", gate="g", anchor_sha="x", body="b")
            )

        other_thread = client.portal.call(thread)

        async def forced_listener():
            # As test_egress_docker's launch: a Linux host's containers reach
            # the host at the bridge's gateway, never its loopback.
            await app.state.egress_tls.close()
            app.state.egress_tls = channel.TLSListener(
                app.state.egress_channels, run_dirs, host="0.0.0.0"
            )
            await app.state.egress_tls.start()

        if probed.socket_channel and not runtime.socket_channel:
            client.portal.call(forced_listener)

        def go(script: str):
            sid = f"s{os.getpid()}"

            async def launch():
                status = await sp.run_task(
                    app.state.db,
                    run_dirs,
                    session_id=sid,
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point=PATH,
                    cmd=["sh", "-c", script.replace("{other_thread}", other_thread)],
                    cwd=work,
                    sandbox={
                        "kind": "docker",
                        "image": IMAGE,
                        "network": {"runtime": {"allow": ["x.io"]}},
                    },
                )
                return status, app.state.db.read(lambda c: events.read_after(c, 0, "w1"))

            status, evs = client.portal.call(launch)
            log = run_dirs.logs / f"{sid}.log"
            assert status != "config_error", log.read_text() if log.exists() else status
            out = work / "out"
            return (out.read_text() if out.exists() else ""), evs

        yield go


def test_kraft_item_progress_in_the_container_reports_for_its_own_item(daemon):
    out, evs = daemon('kraft item progress 2 > out 2>&1; echo " rc=$?" >> out')

    assert out.endswith(" rc=0\n"), out
    assert [e["payload"] for e in evs if e["type"] == "plan_progress"] == [
        {"node_id": "implementation", "task": 2, "total": 2, "title": "serve"}
    ]


def test_a_codex_hook_in_the_container_is_denied_by_the_daemon(daemon):
    """Through the real channel: the gate records the decision. Kraft
    unreachable would print the same deny, from the shim alone."""
    stdin = json.dumps(_CODEX_BASH).replace("'", "'\\''")
    out, evs = daemon(
        f"printf '%s' '{stdin}' | kraft admin permission-hook codex > out; echo \" rc=$?\" >> out"
    )

    body, _, rc = out.rpartition(" rc=")
    assert json.loads(body)["hookSpecificOutput"]["permissionDecision"] == "deny", out
    assert rc == "0\n", out
    decided = [e["payload"] for e in evs if e["type"] == "permission_decision"]
    assert [(d["tool"], d["harness"]) for d in decided] == [("Bash", "codex")]


def test_a_reply_on_another_items_thread_is_refused(daemon):
    out, evs = daemon('kraft item reply {other_thread} --body hi > out 2>&1; echo " rc=$?" >> out')

    assert "403" in out and not out.endswith(" rc=0\n"), out
    refused = [e["payload"] for e in evs if e["type"] == SANDBOX_EGRESS_REFUSED]
    assert len(refused) == 1 and "/replies" in json.dumps(refused[0]), refused
