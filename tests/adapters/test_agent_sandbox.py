"""`kraft.adapters.agent` launching into a docker sandbox: a tool policy is
enforced in the container or the launch is refused, never run unenforced."""

import json

import pytest

from kraft import harness
from kraft.adapters.agent import LaunchRefused
from kraft.paths import RunDirs

BARE = {"kind": "docker", "image": "x"}
#: No `network:`, knowingly: the launch runs, and the checks below are the ones
#: that still apply to it.
DOCKER = {**BARE, "unrestricted_network": True}
NETWORKED = {**BARE, "network": {"runtime": {"allow": ["x.io"]}}}
PROVIDERS = sorted(p.stem for p in harness.BUNDLED.glob("*.yaml"))


@pytest.mark.parametrize("harness", ["codex", "cursor"])
@pytest.mark.parametrize(
    "policy",
    [
        {"deny_tools": ("Bash",)},
        # Web tools never reach either hook, so an allowlist must name them.
        {"allowed_tools": ("Read", "WebFetch", "WebSearch")},
    ],
    ids=["deny", "allowlist"],
)
def test_a_hook_enforced_policy_is_refused_in_a_sandbox_without_network(
    run, tmp_path, harness, policy
):
    """The hook reaches Kraft only through the session's channel; without one
    it crashes, and a crashed hook lets the call through. `network:` is the fix."""
    with pytest.raises(LaunchRefused, match="cannot run inside a sandbox without `network:`"):
        run(
            harness=harness,
            command=harness,
            run_dirs=RunDirs(base=tmp_path),
            sandbox=DOCKER,
            **policy,
        )


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_sandbox_without_network_is_refused_unless_unrestricted(run, tmp_path, provider):
    """Nothing keeps a container with no `network:` from dialing Kraft, so
    gates are not enforced: refused for every harness, naming both fixes,
    unless the repo says it accepts that. (claude is refused on its own
    account, below.)"""
    args = dict(harness=provider, command=provider, run_dirs=RunDirs(base=tmp_path))
    with pytest.raises(
        LaunchRefused, match=r"/repo.*add a `network:` policy.*unrestricted_network"
    ):
        run(**args, sandbox=BARE)
    if provider != "claude":
        assert run(**args, sandbox=DOCKER)["sandbox"] == DOCKER


def test_a_proxy_unaware_harness_under_network_is_refused_before_launch(run, tmp_path):
    """Its only route out would be a proxy it ignores (spec §4)."""
    with pytest.raises(LaunchRefused, match=r"'cursor'.*`proxy_aware: false`"):
        run(harness="cursor", command="agent", run_dirs=RunDirs(base=tmp_path), sandbox=NETWORKED)
    assert run(harness="cursor", command="agent", run_dirs=RunDirs(base=tmp_path), sandbox=DOCKER)


def test_a_hooked_harness_with_nothing_to_enforce_still_runs_sandboxed(run, tmp_path):
    seen = run(harness="codex", command="codex", run_dirs=RunDirs(base=tmp_path), sandbox=DOCKER)
    assert seen["sandbox"] == DOCKER


def test_an_amp_rules_file_is_mounted_read_only_in_the_sandbox(run, tmp_path):
    """Amp silently falls back to its built-in rules for a settings file it
    cannot find, and the worker must not rewrite the one it can."""
    seen = run(
        harness="amp",
        command="amp",
        run_dirs=RunDirs(base=tmp_path),
        sandbox=DOCKER,
        deny_tools=("Bash",),
    )
    assert seen["ro_paths"] == (str(tmp_path / "harness-config" / "amp" / "w1-s1.json"),)


@pytest.mark.parametrize(
    "home", ["items/w1/sandbox-home", "sandbox-home/w1"], ids=["no-old-home", "old-home-exists"]
)
def test_a_sandboxed_item_gets_a_cli_config_dir_of_its_own(run, tmp_path, home):
    """The shared directory would be one item's worker's to rewrite under
    every other item's launches; the item's sandbox home is already mounted.
    That home is an older item's `sandbox-home/<id>/` while it exists (its
    chats are there to resume), else the item's own folder's."""
    if home.startswith("sandbox-home/"):
        (tmp_path / home).mkdir(parents=True)
    seen = run(harness="cursor", command="agent", run_dirs=RunDirs(base=tmp_path), sandbox=DOCKER)
    own = tmp_path / home / ".kraft-harness-config" / "cursor"
    assert seen["env"]["CURSOR_CONFIG_DIR"] == str(own)
    assert (own / "cli-config.json").is_file()
    unsandboxed = run(harness="cursor", command="agent", run_dirs=RunDirs(base=tmp_path))
    assert unsandboxed["env"]["CURSOR_CONFIG_DIR"] == str(tmp_path / "harness-config" / "cursor")


@pytest.mark.parametrize(
    ("link", "refused"),
    [
        (".kraft-harness-config", True),
        (".kraft-harness-config/cursor", True),
        (".kraft-harness-config/cursor/cli-config.json", False),
        (".kraft-harness-config/cursor/.cli-config.json.w1-s1", False),
    ],
    ids=["config-folder", "harness-folder", "config-file", "temporary-file"],
)
def test_a_link_in_the_sandbox_home_is_never_written_through(run, tmp_path, link, refused):
    """The sandbox home is the worker's: a link it planted on the way to the
    config folder refuses the launch, naming it, and one at a file's name is
    replaced. What either points at is never written."""
    home = tmp_path / "items" / "w1" / "sandbox-home"
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "cli-config.json").write_text("untouched")
    planted = home / link
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.symlink_to(victim if refused else victim / "cli-config.json")
    launch = dict(harness="cursor", command="agent", run_dirs=RunDirs(base=tmp_path))

    if refused:
        with pytest.raises(LaunchRefused, match=r"\.kraft-harness-config/cursor"):
            run(**launch, sandbox=DOCKER)
    else:
        run(**launch, sandbox=DOCKER)
        assert not (home / ".kraft-harness-config/cursor/cli-config.json").is_symlink()
    assert [p.name for p in victim.iterdir()] == ["cli-config.json"]
    assert (victim / "cli-config.json").read_text() == "untouched"


@pytest.mark.parametrize(
    "sandbox, mode, expected",
    [
        (DOCKER, None, "danger-full-access"),
        (DOCKER, "workspace-write", "danger-full-access"),
        (DOCKER, "read-only", "read-only"),
        (None, None, "workspace-write"),
    ],
    ids=["sandboxed-default", "sandboxed-default-named", "sandboxed-own-mode", "host"],
)
def test_codex_leaves_isolation_to_the_container(run, tmp_path, sandbox, mode, expected):
    """Codex's bubblewrap cannot create a namespace under Docker's seccomp
    profile, so every shell command fails; a mode the launch chose itself is
    kept."""
    seen = run(
        harness="codex",
        command="codex",
        run_dirs=RunDirs(base=tmp_path),
        sandbox=sandbox,
        **({"permission_mode": mode} if mode else {}),
    )
    assert f"sandbox_mode={expected}" in seen["cmd"]


@pytest.mark.parametrize(
    ("sandbox", "servers"),
    [
        (NETWORKED, {"kraft": {"type": "http", "url": "http://kraft/mcp"}}),
        (None, None),
    ],
    ids=["network", "host"],
)
def test_claude_under_network_asks_its_session_mcp_server_alone(run, tmp_path, sandbox, servers):
    """The host's registration of Kraft's MCP server is out of the
    container's reach; its channel reaches the session's own. On the host
    the launch is as it was."""
    cmd = run(harness="claude", run_dirs=RunDirs(base=tmp_path), sandbox=sandbox)["cmd"]
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "mcp__kraft__permission_request"
    if servers is None:
        assert "--mcp-config" not in cmd and "--strict-mcp-config" not in cmd
    else:
        at = cmd.index("--strict-mcp-config")
        assert cmd[at + 1] == "--mcp-config"
        assert json.loads(cmd[at + 2]) == {"mcpServers": servers}


def test_claude_in_a_sandbox_without_network_is_refused_before_launch(run, tmp_path):
    """Every claude launch names Kraft's MCP permission tool, and a sandbox
    without `network:` has no route to any Kraft MCP server: the CLI exits 1
    at start naming the missing tool (spike 5.5). Refused, naming the fix."""
    with pytest.raises(LaunchRefused, match=r"'claude'.*give the sandbox a `network:`"):
        run(harness="claude", run_dirs=RunDirs(base=tmp_path), sandbox=DOCKER)


def test_a_named_credential_is_launched_as_its_harness_declares_it(run, tmp_path):
    """Ruling E2: `- env: NAME` takes the harness's declaration, sentinel and
    all, and the launch hands it on to be managed."""
    sandbox = {**NETWORKED, "credentials": [{"env": "ANTHROPIC_API_KEY"}]}
    seen = run(harness="claude", command="claude", run_dirs=RunDirs(base=tmp_path), sandbox=sandbox)
    (credential,) = harness.manage(harness.sandbox_credentials(sandbox), seen["declared"])
    assert credential.sentinel == "sk-ant-api03-kraft-proxy-managed"
    assert [(i.domain, i.header) for i in credential.inject] == [("api.anthropic.com", "x-api-key")]
