"""`kraft.api.config_check`: an unsaved config buffer checked exactly as saving
it would be, with nothing written."""

from __future__ import annotations

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


def test_notify_enabled_without_a_url(ctx):
    [issue] = config_check.check("notify.yaml", "enabled: true\n", ctx)
    assert issue.message == "set a webhook URL before enabling notifications"


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
