"""The permission tool a host Claude launch names is the one registered.
`HOME` registers `kraft` at user scope (conftest's `_isolated_kraft_home`);
these take it away."""

from __future__ import annotations

import dataclasses
import json
import os
import shlex
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.harness import commit_all, entry_of, make_repo, write

from kraft import harness as _harness
from kraft import registration
from kraft.adapters import agent


@pytest.fixture
def kraft_on_path(monkeypatch, tmp_path) -> Path:
    """A `kraft` first on the daemon's PATH, which a worker's PATH is (`worker_env`):
    what a registration that runs it by name needs, whatever this machine has."""
    stub = write(tmp_path / "bin", "kraft", "#!/bin/sh\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub.parent}{os.pathsep}{os.environ['PATH']}")
    return stub


def test_a_plugin_only_install_launches_with_the_plugins_tool_name(run, kraft_on_path):
    """The Kraft plugin's server namespaces its tools (claude 2.1.281), so the
    direct name would fail the first permission ask: the 0-token stop."""
    home = Path.home()
    (home / ".claude.json").unlink()
    (home / ".claude" / "plugins").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text('{"enabledPlugins": {"kraft@kraft": true}}')
    (home / ".claude" / "plugins" / "installed_plugins.json").write_text(
        '{"version": 2, "plugins": {"kraft@kraft": [{"scope": "user"}]}}'
    )
    cmd = run()["cmd"]
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == (
        "mcp__plugin_kraft_kraft__permission_request"
    )


def test_a_claude_launch_with_no_kraft_server_is_refused_naming_the_fix(run):
    (Path.home() / ".claude.json").unlink()
    with pytest.raises(agent.LaunchRefused, match="install the Kraft plugin"):
        run()


KRAFT = {"command": "kraft", "args": ["admin", "mcp"]}
LOCAL = {"command": sys.executable, "args": ["-m", "kraft", "admin", "mcp"]}
SECRET = "s3cret-token"
#: A person's own servers carry tokens: none may reach the argv, which the
#: session's row, the API and /proc all show.
DOCS = {"command": "docs-mcp", "env": {"DOCS_TOKEN": SECRET}}
API = {"type": "http", "url": "https://api.example", "headers": {"Authorization": SECRET}}
#: Committed beside `kraft`: Claude asks to approve it per folder, so it is never handed.
TEAM = {"command": "team-mcp"}
#: A harness file written before `extra_mcp_config` existed.
OLD_FILE = "refused"


@pytest.mark.parametrize(
    ("scope", "local", "handed", "old_file"),
    [
        pytest.param("committed-mcp-json", None, {"kraft": KRAFT}, False, id="committed-mcp-json"),
        pytest.param("local-scope", {"kraft": LOCAL}, {"kraft": LOCAL}, False, id="local-scope"),
        pytest.param(
            "local-scope",
            {"kraft": LOCAL, "docs": DOCS, "api": API},
            {"kraft": LOCAL, "docs": DOCS, "api": API},
            False,
            id="local-scope-and-others",
        ),
        pytest.param("user-scope", None, None, False, id="user-scope"),
        pytest.param(
            "user-scope", {"docs": DOCS}, {"docs": DOCS}, False, id="user-scope-local-another"
        ),
        # Only its own servers load, in the checkout too.
        pytest.param("managed", {"docs": DOCS}, None, False, id="managed"),
        # Without `kraft` the session fails its first permission ask: refused at start.
        pytest.param(
            "local-scope", {"kraft": LOCAL}, OLD_FILE, True, id="old-harness-file-needs-kraft"
        ),
        # Kraft loads anywhere; only the person's own servers are missed.
        pytest.param("user-scope", {"docs": DOCS}, None, True, id="old-harness-file-only-others"),
    ],
)
def test_a_copy_launch_finds_the_checkouts_registration_and_is_handed_it(
    run, in_place, kraft_on_path, tmp_path, scope, local, handed, old_file
):
    """The copy is empty at launch and Claude keys a project's servers by its
    folder path: the lookup is of the person's checkout, and the servers tied
    to it are handed in a file only Kraft's user reads, beside the ones that
    load anywhere rather than instead of them. A registration that loads
    anywhere is left alone, even beside a committed `.mcp.json`."""
    repo = make_repo(tmp_path)
    write(repo, ".mcp.json", json.dumps({"mcpServers": {"kraft": KRAFT, "team": TEAM}}))
    commit_all(repo)
    user = Path.home() / ".claude.json"
    if scope == "committed-mcp-json":
        user.unlink()
    elif scope == "managed":
        registration.MANAGED_MCP.write_text(json.dumps({"mcpServers": {"kraft": KRAFT}}))
    if local is not None:
        config = json.loads(user.read_text()) if scope != "local-scope" else {}
        config["projects"] = {str(repo.resolve()): {"mcpServers": local}}
        user.write_text(json.dumps(config))
    row = {"id": "w1", "branch": None, "repo": str(repo)}
    kw = {}
    if old_file:
        hs = _harness.load(None)
        claude = hs.valid["claude"]
        caps = {k: v for k, v in claude.capabilities.items() if k != "extra_mcp_config"}
        hs.valid["claude"] = dataclasses.replace(claude, capabilities=caps)
        kw["harnesses"] = hs

    copy = tmp_path / "copy"
    launch = dict(db=SimpleNamespace(read=lambda fn: row), cwd=str(copy), in_copy=True, **kw)
    if handed == OLD_FILE:
        with pytest.raises(agent.LaunchRefused, match="extra_mcp_config"):
            run(**launch)
        return
    cmd = run(**launch)["cmd"]

    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "mcp__kraft__permission_request"
    assert "--strict-mcp-config" not in cmd
    assert SECRET not in shlex.join(cmd)
    if handed:
        handed_at = Path(cmd[cmd.index("--mcp-config") + 1])
        assert handed_at.parent == copy.parent, "the session's own folder, never the copy"
        assert stat.S_IMODE(handed_at.stat().st_mode) == 0o600
        assert json.loads(handed_at.read_text()) == {"mcpServers": handed}
    else:
        assert "--mcp-config" not in cmd


HTTP = {"type": "http", "url": "http://127.0.0.1:8765/mcp"}


@pytest.mark.parametrize(
    ("server", "on_path", "launch", "refused"),
    [
        pytest.param(KRAFT, True, {}, False, id="bare-on-path"),
        pytest.param(KRAFT, False, {}, True, id="bare-missing"),
        pytest.param(LOCAL, False, {}, False, id="absolute-present"),
        pytest.param({"command": "/nowhere/bin/kraft"}, True, {}, True, id="absolute-missing"),
        pytest.param(HTTP, False, {}, False, id="http-has-nothing-to-check"),
        pytest.param(KRAFT, True, {"env": {"PATH": "/nowhere"}}, True, id="the-repos-env-path"),
        # The machine's own PATH starts it there, not the server's.
        pytest.param(KRAFT, False, {"runner": "gpu"}, False, id="on-a-machine"),
    ],
)
def test_a_launch_whose_kraft_server_cannot_start_on_the_workers_path_is_refused(
    run, kraft_on_path, monkeypatch, tmp_path, server, on_path, launch, refused
):
    """Claude starts the registered server by its command, on the PATH the
    worker gets: one missing there fails the session's first permission ask,
    so the launch is refused before it starts, naming the command and where it
    is registered. A repo's `env:` PATH is the one the worker gets."""
    if not on_path:
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (Path.home() / ".claude.json").write_text(json.dumps({"mcpServers": {"kraft": server}}))
    env = launch.get("env")
    kw = {"repo_entry": entry_of({"env": env})} if env else {"runner": launch.get("runner")}
    if refused:
        with pytest.raises(agent.LaunchRefused) as raised:
            run(**kw)
        assert repr(server["command"]) in str(raised.value)
        assert ".claude.json" in str(raised.value) and "absolute path" in str(raised.value)
        return
    cmd = run(**kw)["cmd"]
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "mcp__kraft__permission_request"
