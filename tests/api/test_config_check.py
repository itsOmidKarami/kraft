"""`kraft.api.config_check`: an unsaved config buffer checked exactly as saving
it would be, with nothing written."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kraft.api import config_check

pytestmark = pytest.mark.api_client(default_setup=False)


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def ctx(client):
    return config_check.context(client.app.state)


GOOD_CHAIN = (
    "id: solo\n"
    "nodes:\n"
    "  - id: run\n"
    "    kind: exec\n"
    "    tasks:\n"
    "      - id: t\n"
    "        kind: subprocess\n"
    "        command: 'true'\n"
)


def test_a_good_chain_has_no_issues(ctx):
    assert config_check.check("chains/solo.yaml", GOOD_CHAIN, ctx) == []


def test_an_unknown_extends_is_an_issue_at_that_line(ctx):
    text = GOOD_CHAIN.replace(
        "        kind: subprocess\n        command: 'true'\n", "        extends: no_such_task\n"
    )
    [issue] = config_check.check("chains/solo.yaml", text, ctx)
    assert issue.loc == ("nodes", 0, "tasks", 0, "extends")
    assert "no_such_task" in issue.message


def test_a_yaml_syntax_error_carries_the_parser_mark(ctx):
    [issue] = config_check.check("chains/solo.yaml", "id: solo\nnodes: [unclosed\n", ctx)
    assert issue.mark is not None


@pytest.mark.parametrize("text", ["- a\n- b\n", "42\n"])
def test_a_non_mapping_is_one_issue(ctx, text):
    [issue] = config_check.check("policy.yaml", text, ctx)
    assert issue.message == "a Kraft config file is a mapping"


@pytest.mark.parametrize("text", ["", "# only a comment\n"])
def test_an_empty_buffer_is_checked_as_an_empty_mapping(ctx, text):
    # An empty theme is all defaults: valid. An empty chain has no `nodes`.
    assert config_check.check("theme.yaml", text, ctx) == []
    assert config_check.check("chains/solo.yaml", text, ctx) != []


def test_a_chain_when_the_library_does_not_load(ctx):
    ctx = config_check.CheckContext(**{**ctx.__dict__, "library": None})
    [issue] = config_check.check("chains/solo.yaml", GOOD_CHAIN, ctx)
    assert "library.yaml" in issue.message


def test_a_chain_declaring_another_id(ctx):
    [issue] = config_check.check("chains/other.yaml", GOOD_CHAIN, ctx)
    assert issue.loc == ("id",)


def test_a_policy_type_error_is_located(ctx):
    [issue] = config_check.check(
        "policy.yaml", "default: {attempts: 3, wall_clock_s: 3600}\nmax_concurrent: many\n", ctx
    )
    assert issue.loc[:1] == ("max_concurrent",)


def test_access_off_localhost_without_a_password(ctx):
    [issue] = config_check.check("access.yaml", "bind: 0.0.0.0\nport: 8765\n", ctx)
    assert issue.message == "set a password before binding off localhost"


def test_access_names_an_allowed_host_that_never_matches(ctx):
    """`Access` loads such an entry as written, so this is where it shows."""
    [issue] = config_check.check("access.yaml", "allowed_hosts: [kraft.local, '*.ts.net']\n", ctx)
    assert issue.loc == ("allowed_hosts", 1)
    assert issue.message.startswith("allowed_hosts: '*.ts.net' is not a host name or IP address")


def test_notify_enabled_without_a_url(ctx):
    [issue] = config_check.check("notify.yaml", "enabled: true\n", ctx)
    assert issue.message == "set a webhook URL before enabling notifications"


def test_notify_unknown_event_reads_like_the_notify_route(ctx):
    [issue] = config_check.check("notify.yaml", "events: [gate_requsted]\n", ctx)
    assert issue.message == (
        "notify.yaml: events: unknown event type 'gate_requsted'; see the Events reference "
        "for the names"
    )
    assert issue.loc == ("events",)


def test_intake_interval_below_the_floor(ctx):
    issues = config_check.check(
        "intake.yaml", "enabled: true\ninterval_s: 5\npriority_ceiling: 2\n", ctx
    )
    assert issues and issues[0].loc[:1] == ("interval_s",)


def test_a_repo_entry_error_is_located_at_its_entry(ctx):
    issues = config_check.check("repos.yaml", "repos:\n  - {}\n", ctx)
    assert issues and issues[0].loc[:2] == ("repos", 0)


@pytest.mark.parametrize(
    "relative", ["../access.yaml", "chains/../../x.yaml", "chains/Bad Name.yaml", "notes.txt"]
)
def test_a_path_that_is_not_a_config_file_is_refused(ctx, relative):
    with pytest.raises(ValueError):
        config_check.check(relative, "a: 1\n", ctx)


def test_checking_writes_nothing(ctx, templates_dir):
    before = snapshot(templates_dir)
    for relative in [*sorted(config_check.FILES), "chains/solo.yaml"]:
        config_check.check(relative, "garbage: [\n", ctx)
        config_check.check(relative, GOOD_CHAIN, ctx)
    assert snapshot(templates_dir) == before


def test_a_library_check_without_a_running_library_still_resolves_the_chains(ctx):
    """A daemon whose library did not load has no chains in memory: the check
    reads them from disk, so an edit that breaks one is still reported."""
    ctx = config_check.CheckContext(**{**ctx.__dict__, "library": None})
    issues = config_check.check("library.yaml", "tasks: {}\n", ctx)
    assert "default" in {i.chain for i in issues}


def test_lint_reports_a_profile_its_harness_has_no_model_for(tmp_path):
    """`profile: fast` has no Codex model: a task pairing it with a codex
    harness used to lint clean and fail only at launch (Kraft-9efnk.34)."""
    shutil.copytree(Path(__file__).resolve().parents[2] / "config", tmp_path, dirs_exist_ok=True)
    library = tmp_path / "library.yaml"
    text = library.read_text()
    assert "harness: claude\n    profile: strong" in text
    library.write_text(
        text.replace("harness: claude\n    profile: strong", "harness: codex\n    profile: fast", 1)
    )
    report = config_check.lint_report(tmp_path)
    assert report["valid"] is False
    assert any(
        "profile 'fast' has no model for provider 'codex'" in i["message"] for i in report["issues"]
    )


def _with_icon(icon):
    return GOOD_CHAIN.replace("      - id: t\n", f"      - id: t\n        icon: {icon}\n")


def test_an_unknown_icon_is_a_lint_issue_at_its_path_and_a_known_one_is_not(ctx, client):
    """R32: lint, not a load failure -- the model takes any kebab-case name."""
    assert config_check.check("chains/solo.yaml", _with_icon("hammer"), ctx) == []
    [issue] = config_check.check("chains/solo.yaml", _with_icon("no-such-icon"), ctx)
    assert issue.message == "run.main.t: unknown icon 'no-such-icon'"
    assert issue.loc == ("nodes", 0, "tasks", 0, "icon")
    saved = client.put("/api/templates/chains/solo", json={"text": _with_icon("no-such-icon")})
    assert saved.status_code == 422
    assert saved.json()["detail"] == issue.message


def _library_with_icon(templates_dir):
    text = (templates_dir / "library.yaml").read_text()
    return text.replace("  implementer:\n", "  implementer:\n    icon: gone\n", 1)


def test_a_library_icon_is_linted_as_its_component(ctx):
    [issue] = config_check.check("library.yaml", _library_with_icon(ctx.templates_dir), ctx)
    assert issue.message == "tasks.implementer: unknown icon 'gone'"


def test_lint_reports_an_unknown_icon_in_a_chain_and_in_the_library(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[2] / "config", tmp_path, dirs_exist_ok=True)
    (tmp_path / "chains" / "solo.yaml").write_text(_with_icon("no-such-icon"))
    (tmp_path / "library.yaml").write_text(_library_with_icon(tmp_path))
    messages = {i["message"] for i in config_check.lint_report(tmp_path)["issues"]}
    assert messages == {
        "run.main.t: unknown icon 'no-such-icon'",
        "tasks.implementer: unknown icon 'gone'",
    }


def test_lint_report_with_and_without_the_instance_plugins(tmp_path):
    """The route lints against the installed plugins; `--dir` has none and
    reports what it could not judge instead of calling it broken."""
    from support.plugins import AGENT, chain, home, install, make_collection

    from kraft.plugins import load

    config = home(tmp_path, chains={"local": chain("release:base")})
    plugins = tmp_path / "run" / "plugins"
    release = {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}}
    install(config, plugins, make_collection(tmp_path, {"release": release}), "release")

    online = config_check.lint_report(config, plugins=load.installed(config, plugins))
    assert (online["valid"], online["chains"], online["unchecked"]) == (
        True,
        ["release:ship", "local"],
        [],
    )
    offline = config_check.lint_report(config, plugins=None)
    assert (offline["valid"], offline["chains"]) == (True, [])
    assert [u["message"].endswith("not checked (plugin)") for u in offline["unchecked"]] == [True]
