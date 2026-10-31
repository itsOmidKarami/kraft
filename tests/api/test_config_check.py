"""`kraft.api.config_check`: an unsaved config buffer checked exactly as saving
it would be, with nothing written."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from kraft.addons import update as addon_update
from kraft.api import config_check, deps
from kraft.config import write_yaml

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


def test_update_names_a_bad_range(ctx):
    [issue] = config_check.check("update.yaml", "allowed_hours: ['1-6']\n", ctx)
    assert issue.loc == ("allowed_hours",)
    assert "1-6" in issue.message


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
    ("text", "repos", "problem"),
    [
        ("workers:\n  a:\n    ssh: h\n", "repos: []\n", None),
        ("workers:\n  a:\n    ssh: h\n    repos: [kraft]\n", "repos:\n  - path: /r/kraft\n", None),
        (
            "workers:\n  a:\n    ssh: h\n    repos: [nope]\n",
            "repos:\n  - path: /r/kraft\n",
            "nope",
        ),
        ("workers:\n  a:\n    capacity: 2\n", "repos: []\n", "exactly one of"),
    ],
    ids=["valid", "repo-resolves", "repo-unknown", "no-mode"],
)
def test_workers_yaml_is_checked_and_its_repos_must_resolve(
    ctx, templates_dir, text, repos, problem
):
    (templates_dir / "repos.yaml").write_text(repos)
    issues = config_check.check("workers.yaml", text, ctx)
    assert (problem is None) == (issues == [])
    if problem:
        assert problem in issues[0].message


_NAMES_A = "repos:\n  - {path: /r, forge: github, forge_account: a}\n"
_NO_ACCOUNTS = "accounts: {}\n"
_HAS_A = "accounts:\n  a: {forge: github, web_url: 'https://github.com'}\n"
_HAS_B = "accounts:\n  b: {forge: github, web_url: 'https://github.com'}\n"


@pytest.mark.parametrize(
    ("on_disk_repos", "on_disk_forges", "name", "text", "problem"),
    [
        ("repos: []\n", _HAS_A, "repos.yaml", _NAMES_A, None),
        ("repos: []\n", _NO_ACCOUNTS, "repos.yaml", _NAMES_A, "names no account"),
        (_NAMES_A, _NO_ACCOUNTS, "repos.yaml", _NAMES_A + "  - {path: /s}\n", None),
        (_NAMES_A, _HAS_A, "forges.yaml", _HAS_B, "names no account"),
        (_NAMES_A, _NO_ACCOUNTS, "forges.yaml", _HAS_B, None),
    ],
    ids=[
        "repos-account-exists",
        "repos-new-unknown-account-refused",
        "repos-old-mismatch-does-not-block",
        "forges-new-mismatch-refused",
        "forges-old-mismatch-does-not-block",
    ],
)
def test_a_save_is_refused_only_for_a_forge_account_problem_it_adds(
    ctx, templates_dir, on_disk_repos, on_disk_forges, name, text, problem
):
    (templates_dir / "repos.yaml").write_text(on_disk_repos)
    (templates_dir / "forges.yaml").write_text(on_disk_forges)
    issues = config_check.check(name, text, ctx)
    assert (problem is None) == (issues == [])
    if problem:
        assert problem in issues[0].message


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


def test_lint_report_with_and_without_the_instance_addons(tmp_path):
    """The route lints against the installed add-ons; `--dir` has none and
    reports what it could not judge instead of calling it broken."""
    from support.addons import AGENT, chain, home, install, make_collection

    from kraft.addons import load

    config = home(tmp_path, chains={"local": chain("release:base")})
    addons = tmp_path / "run" / "addons"
    release = {"library": {"tasks": {"base": AGENT}}, "chains": {"ship": chain("base")}}
    install(config, addons, make_collection(tmp_path, {"release": release}), "release")

    online = config_check.lint_report(config, addons=load.installed(config, addons))
    assert (online["valid"], online["chains"], online["unchecked"]) == (
        True,
        ["release:ship", "local"],
        [],
    )
    offline = config_check.lint_report(config, addons=None)
    assert (offline["valid"], offline["chains"]) == (True, [])
    assert [u["message"].endswith("not checked (add-on)") for u in offline["unchecked"]] == [True]


# ── addons.yaml, and the files whose edit can drop an add-on ──


@pytest.fixture
def with_release(client, tmp_path):
    """The running server with `release@acme` installed and loaded: a task
    capped at 8 dollars, and a `requires` on the agent profile `extra`."""
    from support.addons import AGENT, chain, install, make_collection

    st = client.app.state
    harnesses = yaml.safe_load((st.templates_dir / "harnesses.yaml").read_text())
    harnesses["profiles"]["extra"] = {"model": {"codex": "m"}}
    (st.templates_dir / "harnesses.yaml").write_text(yaml.safe_dump(harnesses))
    release = {
        "library": {"tasks": {"base": {**AGENT, "policy": {"budget_usd": 8}}}},
        "chains": {"ship": chain("base")},
        "manifest": {"requires": {"kraft": "2", "harnesses": ["codex"], "profiles": ["extra"]}},
    }
    collection = make_collection(tmp_path, {"release": release})
    install(st.templates_dir, st.run_dirs.addons, collection, "release")
    deps._reload_templates(st)
    assert "release:ship" in st.library.chain_ids and not deps.invalid_templates(st)
    return st


def _addons_yaml(st, **addons):
    written = yaml.safe_load((st.templates_dir / "addons.yaml").read_text())
    return yaml.safe_dump({**written, "addons": {**written["addons"], **addons}})


USES_RELEASE = (
    "nodes:\n  - id: n\n    kind: exec\n    tasks:\n      - {id: t, extends: 'release:base'}\n"
)
USES_ANOTHER_TOOLS_SKILL = (
    "nodes:\n  - id: n\n    kind: exec\n    tasks:\n"
    "      - {id: t, kind: agent, harness: codex, prompt: p, skill: 'tools:notes'}\n"
)


#: A chain that reads nothing of an add-on's.
LOCAL = (
    "nodes:\n  - id: n\n    kind: exec\n    tasks:\n"
    "      - {id: t, kind: agent, harness: codex, prompt: p}\n"
)


def _builds_on_release(st, tmp_path):
    """A connected repository whose accepted library lists `release@acme`."""
    from support.addons import AGENT, accept_repo_library, repo_with_library

    app = repo_with_library(
        tmp_path,
        library={"addons": ["release@acme"], "tasks": {"base": AGENT}},
        chains={"ship": {"extends": "release:ship"}},
    )
    accept_repo_library(st.templates_dir, st.run_dirs.addons, app, "app")
    deps._reload_templates(st)


def _builds_on(st, tmp_path, addon, collection, parent):
    """`<addon>@<collection>`, installed and enabled, naming `parent` in
    `requires.addons`."""
    from support.addons import AGENT, chain, install, make_collection

    spec = {
        "library": {"tasks": {"own": AGENT}},
        "chains": {"run": chain("own")},
        "manifest": {"requires": {"kraft": "2", "harnesses": ["codex"], "addons": {parent: "1"}}},
    }
    found = make_collection(tmp_path, {addon: spec}, name=collection)
    install(st.templates_dir, st.run_dirs.addons, found, addon)


def _a_child_of_release(st, tmp_path):
    _builds_on(st, tmp_path, "team", "payments", "release@acme")
    deps._reload_templates(st)


def _a_tree_under_release(st, tmp_path):
    _builds_on(st, tmp_path, "dept", "corp", "release@acme")
    _builds_on(st, tmp_path, "team", "payments", "dept@corp")
    deps._reload_templates(st)


_PARENT_GONE = "its parent release@acme is not loaded: is disabled"


@pytest.mark.parametrize(
    "setup, chain_text, edit, says",
    [
        (None, USES_RELEASE, {"release@acme": False}, "chain mine would stop resolving"),
        (
            _builds_on_release,
            LOCAL,
            {"release@acme": False},
            "cannot disable release@acme: something installed builds on it\n"
            "  repository app: .kraft/library.yaml lists release@acme under addons:",
        ),
        (None, USES_ANOTHER_TOOLS_SKILL, {"tools@acme": True}, "chain mine would stop resolving"),
        (None, USES_RELEASE, {"release@acme": {"auto_update": True}}, None),
        (None, USES_RELEASE, {"release@acme": {"enabeld": False}}, "enabeld"),
        (
            _a_child_of_release,
            LOCAL,
            {"release@acme": False},
            (
                "cannot disable release@acme: something installed builds on it\n"
                "  team@payments builds on it (requires.addons)",
                f"would leave add-on team@payments out: {_PARENT_GONE}",
            ),
        ),
        (_a_child_of_release, LOCAL, {"release@acme": False, "team@payments": False}, None),
        (
            _a_tree_under_release,
            LOCAL,
            {"release@acme": False},
            (
                "  dept@corp builds on it (requires.addons)",
                f"would leave add-on dept@corp out: {_PARENT_GONE}",
                "would leave add-on team@payments out: its parent dept@corp is not loaded: "
                + _PARENT_GONE,
            ),
        ),
    ],
    ids=[
        "breaks-a-chain",
        "a-repo-library-builds-on-it",
        "captures-a-reference",
        "an-edit-that-unloads-nothing",
        "typo",
        "a-child-builds-on-it",
        "parent-and-child-together",
        "names-each-descendant-left-out",
    ],
)
def test_an_addons_yaml_edit_is_checked(with_release, tmp_path, setup, chain_text, edit, says):
    """A hand edit is refused for what it would stop resolving, with the reason,
    and, as `kraft admin addon disable` is, while a repo library or a child
    add-on builds on the add-on it unloads. Each descendant it would leave out
    is named with why."""
    st = with_release
    if setup:
        setup(st, tmp_path)
    (st.templates_dir / "chains" / "mine.yaml").write_text(chain_text)
    deps._reload_templates(st)
    assert "mine" not in st.invalid_chains

    issues = config_check.check("addons.yaml", _addons_yaml(st, **edit), config_check.context(st))

    want = (says,) if isinstance(says, str) else says or ()
    said = [i.message for i in issues]
    assert len(said) == len(want) and all(w in m for w, m in zip(want, said, strict=True)), said


def test_a_pending_addons_yaml_edit_is_linted_once(with_release, monkeypatch):
    """`apply.notify` asks again every few seconds: nothing the lint reads has
    changed, so it is not run again; a changed input runs it again."""
    st = with_release
    (st.templates_dir / "chains" / "mine.yaml").write_text(USES_RELEASE)
    deps._reload_templates(st)
    text = _addons_yaml(st, **{"release@acme": False})
    real, calls = addon_update.state_of, []
    monkeypatch.setattr(
        addon_update, "state_of", lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    )
    ctx = config_check.context(st)

    first = config_check.check("addons.yaml", text, ctx)
    assert first, "the edit must break something, or the test pins nothing"
    assert config_check.check("addons.yaml", text, ctx) == first
    assert config_check.check("addons.yaml", text, ctx) == first
    assert len(calls) == 2  # before and after, once

    library = st.templates_dir / "library.yaml"
    library.write_text(library.read_text() + "\n# touched\n")
    config_check.check("addons.yaml", text, ctx)
    assert len(calls) == 4  # a changed input lints again


def test_a_lock_that_does_not_parse_is_an_issue(with_release):
    ctx = config_check.context(with_release)
    lock = (with_release.templates_dir / "addons.lock").read_text()
    assert config_check.check("addons.lock", lock, ctx) == []
    (issue,) = config_check.check(
        "addons.lock", lock.replace("lock_version: 1", "lock_version: 2"), ctx
    )
    assert "lock_version" in issue.message


def _install_child(st, tmp_path):
    """`team@payments`, built on `release@acme`: its task extends the parent's
    `base` and raises the budget to 9 dollars."""
    from support.addons import chain, install, make_collection

    team = {
        "library": {"tasks": {"scan": {"extends": "release:base", "policy": {"budget_usd": 9}}}},
        "chains": {"scan": chain("scan")},
        "manifest": {
            "requires": {"kraft": "2", "harnesses": ["codex"], "addons": {"release@acme": "1"}}
        },
    }
    payments = make_collection(tmp_path, {"team": team}, name="payments")
    install(st.templates_dir, st.run_dirs.addons, payments, "team")
    deps._reload_templates(st)
    assert "team:scan" in st.library.chain_ids and not deps.invalid_templates(st)


@pytest.mark.parametrize(
    "file, edit, says, child",
    [
        (
            "policy.yaml",
            lambda d: d.update(maxima={"tasks": {"budget_usd": 5}}),
            "would leave add-on release@acme out: ",
            False,
        ),
        (
            "policy.yaml",
            lambda d: d.update(maxima={"tasks": {"budget_usd": 8.5}}),
            "would leave add-on team@payments out: ",
            True,
        ),
        (
            "harnesses.yaml",
            lambda d: d["profiles"].pop("extra"),
            "would leave add-on release@acme out: requires agent profile 'extra'",
            False,
        ),
        (
            "repos.yaml",
            lambda d: d.setdefault("repos", []).append({"path": "/r", "id": "release"}),
            "would leave add-on release@acme out: its namespace 'release' is now a repository id",
            False,
        ),
    ],
    ids=[
        "maxima-lowered",
        "maxima-lowered-child-through-parent",
        "required-profile-removed",
        "namespace-becomes-a-repo-id",
    ],
)
def test_an_edit_that_drops_an_addon_is_reported(with_release, tmp_path, file, edit, says, child):
    """Reported on the pending reload, before it is applied. A child's limits
    are judged with its parent's library, which its task extends."""
    st = with_release
    if child:
        _install_child(st, tmp_path)
    written = st.templates_dir / file
    data = (yaml.safe_load(written.read_text()) if written.is_file() else None) or {}
    assert config_check.check(file, yaml.safe_dump(data), config_check.context(st)) == []
    edit(data)

    issues = config_check.check(file, yaml.safe_dump(data), config_check.context(st))

    assert [says in i.message for i in issues] == [True], [i.message for i in issues]


def test_an_edit_that_drops_a_repo_librarys_id_is_reported(client, tmp_path):
    from support.addons import accept_repo_library, repo_with_library

    st = client.app.state
    accept_repo_library(st.templates_dir, st.run_dirs.addons, repo_with_library(tmp_path), "app")
    deps._reload_templates(st)
    written = yaml.safe_load((st.templates_dir / "repos.yaml").read_text())
    assert config_check.check("repos.yaml", yaml.safe_dump(written), config_check.context(st)) == []
    del written["repos"][0]["id"]

    issues = config_check.check("repos.yaml", yaml.safe_dump(written), config_check.context(st))

    assert [i.message for i in issues] == [
        "would leave repo library app out: repository id 'app' is no longer in repos.yaml"
    ]


@pytest.fixture
def two_repos(client, tmp_path):
    """app's repo library accepted (task `base`, chain `ship`, steering
    `tone`, profile `deep`); `web` connected as `web` with no library."""
    from support.addons import AGENT, accept_repo_library, repo_with_library
    from support.harness import make_repo

    st = client.app.state
    deep = {"profiles": {"deep": {"model": {"codex": "gpt-5"}}}}
    app = repo_with_library(
        tmp_path,
        library={"tasks": {"base": AGENT}, "steering": {"tone": {"instructions": "terse"}}},
        extra={".kraft/profiles.yaml": yaml.safe_dump(deep)},
    )
    web = make_repo(tmp_path, "web")
    accept_repo_library(st.templates_dir, st.run_dirs.addons, app, "app")
    repos = yaml.safe_load((st.templates_dir / "repos.yaml").read_text())
    repos["repos"].append({"path": str(web), "id": "web"})
    (st.templates_dir / "repos.yaml").write_text(yaml.safe_dump(repos))
    deps._reload_templates(st)
    return st.templates_dir, {"app": str(app), "web": str(web), "templates": st.templates_dir}


def _chain_with(task):
    return {"id": "mine", "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", **task}]}]}


def _entry(repo, **fields):
    def edit(data, paths):
        (entry,) = [e for e in data["repos"] if e["path"] == paths[repo]]
        entry.update(fields)

    return edit


def _schedule(repo):
    def edit(data, paths):
        schedule = {"cron": "0 0 * * *", "repo": paths[repo], "chain": "app:ship", "title": "t"}
        data.update(enabled=False, interval_s=60, priority_ceiling=2, schedules=[schedule])

    return edit


def _trigger(repo):
    def edit(data, paths):
        trigger = {"cron": "0 0 * * *", "repo": paths[repo], "chain": "app:ship", "title": "t"}
        data["triggers"] = [trigger]

    return edit


def _fallback(data, paths):
    data.setdefault("profiles", {})["spare"] = {
        "model": {"claude": "sonnet"},
        "fallback": [{"profile": "app:deep"}],
    }


def _addon_namespace_as_repo_id(data, paths):
    """`release` is an add-on's namespace (a pending `addons.yaml` entry holds
    it) and also a repo id set by hand: that id names no repo library."""
    addons = {"collections": {"acme": {"git": "https://example.com/acme.git"}}}
    write_yaml(paths["templates"] / "addons.yaml", {**addons, "addons": {"release@acme": True}})
    data["repos"].append({"path": "/release", "id": "release"})
    _entry("web", default_chain="release:ship")(data, paths)


def _library_extends(data, paths):
    data.setdefault("tasks", {})["mine"] = {"extends": "app:base"}


@pytest.mark.parametrize(
    "rel, edit, outside",
    [
        pytest.param(
            "chains/mine.yaml", _chain_with({"extends": "app:base"}), True, id="local-chain-extends"
        ),
        pytest.param("library.yaml", _library_extends, True, id="library-extends"),
        pytest.param("harnesses.yaml", _fallback, True, id="harnesses-fallback"),
        pytest.param(
            "repos.yaml",
            _entry("web", default_chain="app:ship"),
            True,
            id="another-repos-default-chain",
        ),
        pytest.param(
            "repos.yaml", _entry("web", steering=["app:tone"]), True, id="another-repos-steering"
        ),
        pytest.param("intake.yaml", _schedule("web"), True, id="intake-schedule-for-another-repo"),
        pytest.param("policy.yaml", _trigger("web"), True, id="policy-trigger-for-another-repo"),
        pytest.param(
            "chains/mine.yaml",
            _chain_with({"kind": "agent", "harness": "claude", "prompt": "p", "skill": "web:x"}),
            True,
            id="reserved-id-with-no-library",
        ),
        pytest.param(
            "repos.yaml", _entry("app", default_chain="app:ship"), False, id="own-default-chain"
        ),
        pytest.param("repos.yaml", _entry("app", steering=["app:tone"]), False, id="own-steering"),
        pytest.param("intake.yaml", _schedule("app"), False, id="own-schedule"),
        pytest.param(
            "repos.yaml", _addon_namespace_as_repo_id, False, id="addon-namespace-repo-id"
        ),
        pytest.param(
            "chains/mine.yaml",
            _chain_with(
                {"kind": "agent", "harness": "claude", "prompt": "p", "skill": "superpowers:x"}
            ),
            False,
            id="addon-reference",
        ),
    ],
)
def test_a_repo_library_is_named_only_from_its_repo(client, two_repos, rel, edit, outside):
    templates, paths = two_repos
    if callable(edit):
        path = templates / rel
        data = (yaml.safe_load(path.read_text()) if path.is_file() else None) or {}
        edit(data, paths)
    else:
        data = edit

    issues = config_check.check(rel, yaml.safe_dump(data), config_check.context(client.app.state))

    scoped = [i.message for i in issues if "visible only to items in" in i.message]
    owner = "web" if "web:x" in yaml.safe_dump(data) else "app"
    if not outside:
        assert scoped == []
        return
    (message,) = scoped
    assert message.startswith(rel) and message.endswith(f"visible only to items in {owner}")
