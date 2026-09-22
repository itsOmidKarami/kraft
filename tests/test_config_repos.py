"""`config`'s repos.yaml helpers: load/save, the submodule-edge migration, and
the probe `POST /repos` is built on (moved out of tests/api/test_repos.py: none
of these goes through the API)."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from support.harness import make_repo

from kraft import config
from kraft.policy import SandboxPolicy


def _load(tmp_path, *entries):
    path = tmp_path / "repos.yaml"
    path.write_text(yaml.safe_dump({"repos": list(entries)}))
    return config.load_repos(path)


def _set_origin(repo, url):
    subprocess.run(["git", "remote", "add", "origin", url], cwd=repo, check=True)


_SANDBOX = {"kind": "docker", "image": "kraft-worker:node"}


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"gitlab_project": "group/repo"}, {"forge": "gitlab", "project": "group/repo"}),
        ({"forge": "github", "project": "o/r"}, {"forge": "github", "project": "o/r"}),
        (
            {"forge": "github", "project": "o/r", "gitlab_project": "g/r"},
            {"forge": "github", "project": "o/r"},
        ),
        # a hand-edited half-migrated entry: `project` set, `forge` absent
        (
            {"project": "group/kept", "gitlab_project": "group/legacy"},
            {"forge": None, "project": "group/kept"},
        ),
        ({}, {"forge": None, "project": None}),
        ({"forge": "gitea", "project": "t/r"}, {"forge": "gitea"}),
        ({}, {"sandbox": None}),
        ({"sandbox": _SANDBOX}, {"sandbox": SandboxPolicy(**_SANDBOX)}),
        ({"sandbox": False}, {"sandbox": False}),
        ({}, {"models": {}, "areas": {}}),
        (
            {"areas": {"api": {"paths": ["a/**"], "setup": "uv sync"}}},
            {"areas": {"api": {"paths": ["a/**"], "setup": "uv sync"}}},
        ),
        ({"models": {"claude": "opus"}}, {"models": {"claude": "opus"}}),
        # Anything already in a repos.yaml was connected by a human --
        # auto-connect did not exist when it was written. Defaulting to False
        # would hide every repo behind the Detected section on first load.
        ({}, {"managed": True}),
        ({"managed": False}, {"managed": False}),
        ({}, {"local_files": []}),
        ({"local_files": [".python-version"]}, {"local_files": [".python-version"]}),
        ({}, {"setup_command": None, "env": {}, "env_passthrough": []}),
        ({}, {"intent_dir": None}),
        ({"intent_dir": "docs/intent"}, {"intent_dir": "docs/intent"}),
        # Ruling 212: an absent `enabled` is enabled, not disabled.
        ({}, {"enabled": True}),
        ({"enabled": False}, {"enabled": False}),
        ({}, {"name": None, "default_chain_template": None}),
        (
            {"name": "r", "default_chain_template": "quick"},
            {"name": "r", "default_chain_template": "quick"},
        ),
    ],
    ids=[
        "reads-a-legacy-gitlab-project",
        "passes-through-the-new-shape",
        "the-new-shape-wins-over-legacy",
        "an-explicit-project-survives-a-legacy-key",
        "forge-and-project-default-to-none",
        "keeps-an-unknown-forge",
        "sandbox-defaults-to-none",
        "passes-through-a-well-formed-sandbox",
        "passes-through-an-explicit-sandbox-off",
        "models-and-areas-default-to-empty",
        "keeps-an-area-as-written",
        "keeps-a-per-profile-model",
        "managed-defaults-true-for-a-pre-existing-entry",
        "keeps-an-explicit-managed-false",
        "local-files-default-to-empty",
        "keeps-a-declared-local-file",
        "defaults-setup-command-env-and-env-passthrough",
        "intent-dir-defaults-to-none",
        "keeps-an-intent-dir",
        "an-absent-enabled-is-enabled",
        "keeps-an-explicit-enabled-false",
        "name-and-default-chain-template-default-to-none",
        "keeps-a-declared-name-and-default-chain-template",
    ],
)
def test_load_repos_reads_an_entry(tmp_path, entry, expected):
    (repo,) = _load(tmp_path, {"path": "/r", **entry})
    assert {key: getattr(repo, key) for key in expected} == expected
    assert "gitlab_project" not in repo.model_dump()


def test_model_dump_repo_keeps_an_unset_enabled_name_and_chain_template_absent(tmp_path):
    """Ruling 212: `enabled: bool = True` (and `name`, `default_chain_template`)
    carry a typed default so every reader can use the attribute, but a save or
    a `GET /repos` must not fill an absent key in with it -- that would turn a
    read into a write, flipping nothing but appearing to opt every existing
    entry into "enabled" on disk."""
    (repo,) = _load(tmp_path, {"path": "/r"})
    dumped = repo.model_dump_repo()
    assert "enabled" not in dumped
    assert "name" not in dumped
    assert "default_chain_template" not in dumped


def test_model_dump_repo_keeps_an_explicit_value(tmp_path):
    (repo,) = _load(
        tmp_path,
        {"path": "/r", "enabled": False, "name": "r", "default_chain_template": "quick"},
    )
    dumped = repo.model_dump_repo()
    assert dumped["enabled"] is False
    assert dumped["name"] == "r"
    assert dumped["default_chain_template"] == "quick"


@pytest.mark.parametrize(
    ("entry", "match"),
    [
        ({"sandbox": {"kind": "docker"}}, "image"),
        ({"sandbox": "docker"}, "sandbox: must be a mapping, not 'docker'"),
        ({"sandbox": {"kind": "podman", "image": "y"}}, r"known: \['docker'\]"),
        ({"sandbox": {**_SANDBOX, "network": "none"}}, "'network'"),
        ({"steering": ["missing"]}, "missing"),
        ({"managed": "yes"}, "'managed' must be a boolean"),
        ({"local_files": ".python-version"}, "'local_files' must be a list"),
        ({"local_files": ["/etc/passwd"]}, "must be a relative path"),
        ({"local_files": ["../secrets"]}, "must be a relative path"),
        ({"local_files": [".venv/"]}, "must name a file"),
        ({"local_files": ["*.pyc"]}, "must be a literal path, not a glob"),
        ({"local_files": ["**/*.env"]}, "must be a literal path, not a glob"),
        ({"setup_command": ["uv", "sync"]}, "setup_command"),
        ({"env": {"A": 1}}, "'env'"),
        ({"env_passthrough": ["", "OK"]}, "env_passthrough"),
        ({"models": {"Claude Review": "opus"}}, "models"),
        ({"models": {"claude": 4}}, "models"),
        ({"areas": {"api": {"paths": []}}}, "areas"),
        ({"areas": {"api": {"paths": ["a/**"], "forge": {"kind": "github"}}}}, "areas"),
        ({"test_scopes": [{"paths": ["src/**"]}]}, "command"),
        ({"intent_dir": "/abs/intent"}, "must be a relative path inside the repo"),
        ({"intent_dir": "../intent"}, "must be a relative path inside the repo"),
        ({"intent_dir": 3}, "intent_dir"),
        ({"intent_dir": ""}, "intent_dir"),
    ],
    ids=[
        "a-malformed-sandbox",
        "a-sandbox-that-is-no-mapping",
        "a-sandbox-of-an-unknown-kind",
        "a-sandbox-key-nothing-reads",
        "a-missing-steering-file",
        "a-non-boolean-managed",
        "a-non-list-local-files",
        "an-absolute-local-file",
        "a-local-file-escaping-the-repo",
        "a-local-files-directory-entry",
        "a-local-files-glob-star",
        "a-local-files-glob-double-star",
        "a-non-string-setup-command",
        "a-non-flat-string-map-env",
        "an-empty-env-passthrough-entry",
        "a-models-key-that-is-no-profile-id",
        "a-non-string-model",
        "an-area-covering-no-path",
        "an-area-naming-a-forge",
        "a-test-scope-with-no-command",
        "an-absolute-intent-dir",
        "an-intent-dir-escaping-the-repo",
        "a-non-string-intent-dir",
        "an-empty-intent-dir",
    ],
)
def test_load_repos_rejects_an_entry(tmp_path, entry, match):
    with pytest.raises(config.ConfigError, match=match):
        _load(tmp_path, {"path": "/r", **entry})


@pytest.mark.parametrize(
    "legacy",
    [{"default_model": "sonnet"}, {"default_root_merge_policy": "skip"}],
    ids=["default-model", "default-root-merge-policy"],
)
def test_a_retired_repo_key_is_dropped_on_read_and_gone_after_a_save(tmp_path, caplog, legacy):
    """Ruling 165's upgrade path: an existing `repos.yaml` still loads, the
    retired key is not carried along as an unknown extra (it would otherwise
    round-trip forever), a warning names it, and the next save persists the
    new shape. `default_model` has no one-to-one successor -- it was one model
    for every provider -- so it is dropped, not guessed into `models:`."""
    (key,) = legacy
    with caplog.at_level(logging.WARNING, logger="kraft.config"):
        (entry,) = _load(tmp_path, {"path": "/r", **legacy})
    assert key not in entry.model_dump()
    assert entry.models == {}
    assert key in caplog.text
    # Its own warning, never the unrecognised-key one (nor its near-miss
    # refusal): a retired key is known, and where it went is named.
    assert "unrecognised" not in caplog.text
    config.save_repos(tmp_path / "repos.yaml", [entry.model_dump()])
    assert key not in (tmp_path / "repos.yaml").read_text()


def test_repo_entry_keeps_the_messages_the_hand_rolled_loader_gave(tmp_path):
    """Thirteen of repos.yaml's fourteen ConfigError messages name the offending key.
    A model that says 'Input should be a valid boolean' instead is a regression
    an operator pays for at 3am. (Characterization: pinned before the rewrite.)"""
    p = tmp_path / "repos.yaml"
    p.write_text("repos:\n  - path: /r\n    managed: sometimes\n")
    with pytest.raises(config.ConfigError) as exc:
        config.load_repos(p)
    assert "managed" in str(exc.value)
    assert "repos.yaml" in str(exc.value)


@pytest.mark.parametrize("field", ["env_passthrough", "local_files"])
def test_repo_entry_empty_string_items_use_pydantic_inner_constraints(field):
    with pytest.raises(ValidationError) as exc:
        config.RepoEntry.model_validate({"path": "/a", field: [""]})
    assert exc.value.errors()[0]["type"] == "string_too_short"


def test_save_repos_round_trip_drops_legacy_key(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r", "gitlab_project": "group/repo"}]})
    config.save_repos(path, [r.model_dump() for r in config.load_repos(path)])
    assert "gitlab_project" not in path.read_text()
    assert "forge: gitlab" in path.read_text()


def test_load_repos_hands_over_the_model_not_a_dump_of_it(tmp_path):
    """Kraft-5d510.6: every reader gets `RepoEntry`, its nested fields typed,
    so no caller re-parses a dict the loader already validated."""
    from kraft.automated_review import AutomatedReview

    (entry,) = _load(
        tmp_path,
        {
            "path": "/r",
            "test_scopes": [{"paths": ["**"], "command": "just test"}],
            "automated_review": {"bot": "coderabbitai", "check": None},
        },
    )

    assert isinstance(entry, config.RepoEntry)
    assert isinstance(entry.automated_review, AutomatedReview)
    assert isinstance(entry.test_scopes[0], config.TestScope)


def test_a_malformed_sandbox_is_refused_naming_its_entry(tmp_path):
    """Kraft-5d510.1: typed as `SandboxPolicy`, refused in its words, not as
    a three-way union error."""
    with pytest.raises(config.ConfigError) as refused:
        _load(tmp_path, {"path": "/r", "sandbox": {"kind": "podman", "image": "y"}})

    assert str(refused.value) == (
        "repos.yaml: /r: sandbox: kind 'podman' is not supported; known: ['docker']"
    )


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({}, None),
        ({"sandbox": _SANDBOX}, _SANDBOX),
        ({"sandbox": False}, None),
        ({"policy": {"sandbox": _SANDBOX}}, _SANDBOX),
    ],
    ids=["sets-none", "sets-one", "says-off", "sets-one-in-its-policy-block"],
)
def test_an_entrys_sandbox_is_whichever_key_set_it(tmp_path, entry, expected):
    """One answer for both spellings (`_one_sandbox` refuses both at once):
    what the repository layer folds in is what a live launch reads."""
    (repo,) = _load(tmp_path, {"path": "/r", **entry})

    assert repo.effective_sandbox == (SandboxPolicy(**expected) if expected else None)
    layer = repo.repository_override()
    assert (layer.sandbox if layer else None) == repo.effective_sandbox


# ── the legacy `submodules:` edge migration ──


def test_a_configured_submodule_edge_becomes_a_child_repo_entry(tmp_path):
    edge = {
        "path": "libs/a",
        "enabled": True,
        "test_command": "cargo test",
        "chain_override": "quick",
    }
    repos = _load(tmp_path, {"path": "/ws", "name": "ws", "submodules": [edge]})
    assert [r.path for r in repos] == ["/ws", "/ws/libs/a"]
    child = repos[1]
    assert child.name == "a"
    assert child.enabled is True
    assert child.test_command == "cargo test"
    assert child.default_chain_template == "quick"
    # a human had set these values, so the child is a decision, not noise
    assert child.managed is True


def test_an_all_default_submodule_edge_is_dropped(tmp_path):
    # Carries no human decision, so there is nothing to preserve. Task 3's
    # auto-connect re-creates it as managed: false.
    repos = _load(tmp_path, {"path": "/ws", "submodules": [{"path": "libs/a", "enabled": False}]})
    assert [r.path for r in repos] == ["/ws"]


def test_an_existing_child_entry_wins_over_a_legacy_edge(tmp_path):
    repos = _load(
        tmp_path,
        {"path": "/ws", "submodules": [{"path": "libs/a", "test_command": "stale"}]},
        {"path": "/ws/libs/a", "test_command": "real"},
    )
    assert [r.path for r in repos] == ["/ws", "/ws/libs/a"]
    assert repos[1].test_command == "real"


def test_migration_drops_submodules_and_allow_cross_repo_keys(tmp_path):
    edge = {"path": "libs/a", "enabled": True}
    for entry in _load(tmp_path, {"path": "/ws", "allow_cross_repo": True, "submodules": [edge]}):
        assert "submodules" not in entry.model_dump()
        assert "allow_cross_repo" not in entry.model_dump()


def test_migration_is_idempotent(tmp_path):
    once = _load(tmp_path, {"path": "/ws", "submodules": [{"path": "libs/a", "enabled": True}]})
    config.save_repos(tmp_path / "repos.yaml", [r.model_dump() for r in once])
    assert config.load_repos(tmp_path / "repos.yaml") == once


# ── probe_repo ──


@pytest.mark.parametrize(
    ("origin", "forge", "project"),
    [
        (None, None, None),
        ("git@gitlab.com:group/repo.git", "gitlab", "group/repo"),
        ("https://gitlab.com/group/sub/repo.git", "gitlab", "group/sub/repo"),
        ("git@github.com:owner/repo.git", "github", "owner/repo"),
        ("https://github.com/owner/repo", "github", "owner/repo"),
        ("git@git.example.com:team/repo.git", None, None),
    ],
    ids=[
        "no-remote",
        "gitlab-ssh",
        "gitlab-https",
        "github-ssh",
        "github-https",
        "an-unknown-host-is-not-an-error",
    ],
)
def test_probe_detects_the_forge(tmp_path, origin, forge, project):
    repo = make_repo(tmp_path)
    if origin:
        _set_origin(repo, origin)
    probed = config.probe_repo(repo)
    assert (probed["forge"], probed["project"]) == (forge, project)
    assert "gitlab_project" not in probed
    assert probed["name"] == "sample"


def test_probe_survives_a_repo_whose_remote_was_removed(tmp_path):
    """Distinct from test_probe_detects_the_forge[no-remote]: make_repo never
    adds an origin, so without this the `git remote remove` was a silent no-op
    and both tests exercised the same never-had-a-remote state."""
    repo = make_repo(tmp_path)
    _set_origin(repo, "git@gitlab.com:group/repo.git")
    subprocess.run(["git", "remote", "remove", "origin"], cwd=repo, check=True)
    probed = config.probe_repo(repo)
    assert probed["forge"] is None
    assert probed["project"] is None
    assert "gitlab_project" not in probed
    assert Path(probed["path"]) == repo.resolve()


def test_probe_survives_a_gitmodules_that_is_not_utf8(tmp_path):
    """`.gitmodules` is read best-effort — a repo whose submodule list cannot be
    parsed still probes, with no submodules. read_text() raises UnicodeDecodeError,
    a ValueError, which `except OSError` does not catch."""
    repo = make_repo(tmp_path)
    (repo / ".gitmodules").write_bytes(b'[submodule "\xff\xfe libs/x"]\n\tpath = libs/x\n')
    assert config.probe_repo(repo)["submodules"] == []


def test_probe_from_a_worktree_reports_the_main_checkout(tmp_path):
    """An agent's cwd IS a linked worktree, and `/kraft:handoff` tells it to call
    `ensure_repo()` every time. Without this, every handoff registers the
    worktree as a repo of its own — observed live in repos.yaml."""
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(worktree), "-b", "wt-branch"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    assert Path(config.probe_repo(worktree)["path"]) == repo.resolve()


def test_probe_of_a_submodule_stays_the_submodule(tmp_path):
    """A submodule's common dir is `<super>/.git/modules/<path>`, whose parent is
    `<super>/.git/modules` — not a repo at all. The `.git` guard keeps a
    submodule on the `--show-toplevel` answer it has always had."""
    lib = make_repo(tmp_path, name="lib")
    super_repo = make_repo(tmp_path, name="super")
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "libs/sub"],
        cwd=super_repo,
        check=True,
        capture_output=True,
    )
    sub = super_repo / "libs" / "sub"
    assert Path(config.probe_repo(sub)["path"]) == sub.resolve()


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("pyproject.toml", "uv sync"),
        ("package-lock.json", "npm ci"),
        ("yarn.lock", "yarn install --frozen-lockfile"),
        ("pnpm-lock.yaml", "pnpm install --frozen-lockfile"),
        ("Cargo.toml", "cargo fetch"),
        ("go.mod", "go mod download"),
        (None, None),
    ],
    ids=["uv", "npm", "yarn", "pnpm", "cargo", "go", "an-unmarked-repo-gets-nothing"],
)
def test_the_setup_probe_suggests_per_marker(tmp_path, marker, expected):
    if marker:
        (tmp_path / marker).write_text("")
    assert config._first_setup_command(tmp_path) == expected


def test_probe_repo_suggests_a_setup_command(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert config.probe_repo(repo)["setup_command"] == "uv sync"


@pytest.mark.parametrize("name", ["Justfile", "justfile"], ids=["capitalized", "lowercase"])
def test_the_test_probe_recognizes_a_justfile_marker(tmp_path, name):
    """Kraft-reriq: a repo whose tests must go through a Justfile target (like
    Kraft itself: `just test`, never raw pytest) was probed with the wrong
    command -- `pyproject.toml` matched first and suggested plain pytest."""
    (tmp_path / name).write_text("test:\n    pytest\n")
    assert config._first_test_marker(tmp_path) == (name, "just test")


@pytest.mark.parametrize(
    ("justfile", "expected"),
    [
        ("test:\n    pytest\n", ("Justfile", "just test")),
        (
            "set shell := ['zsh']\n[no-cd]\n@test *ARGS: build\n    pytest {{ARGS}}\n",
            ("Justfile", "just test"),
        ),
        ("test-ui:\n    npm test\nlint:\n    ruff\n", ("pyproject.toml", "uv run pytest -q")),
        ("test := 'x'\n", ("pyproject.toml", "uv run pytest -q")),
    ],
    ids=[
        "a-test-recipe",
        "a-test-recipe-with-args-and-attributes",
        "no-test-recipe",
        "a-variable-named-test",
    ],
)
def test_the_test_probe_prefers_the_justfiles_test_recipe_over_pyproject(
    tmp_path, justfile, expected
):
    """Kraft-enc5z: a justfile wins only when `just test` would run something;
    one without a `test` recipe falls through to the manifest beside it."""
    (tmp_path / "Justfile").write_text(justfile)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert config._first_test_marker(tmp_path) == expected


def test_probe_repo_names_the_marker_each_test_command_came_from(tmp_path):
    """Kraft-enc5z: the operator is told what the proposal was read from."""
    repo = make_repo(tmp_path)
    (repo / "justfile").write_text("test:\n    pytest\n")
    (repo / "frontend").mkdir()
    (repo / "frontend" / "package.json").write_text("{}")
    probed = config.probe_repo(repo)
    assert probed["test_command"] == "just test"
    assert probed["test_markers"] == ["justfile", "frontend/package.json"]


@pytest.mark.parametrize(
    ("expected_failure", "warns"),
    [(True, False), (False, True)],
    ids=["an-expected-failure-logs-at-debug", "an-unhandled-failure-still-warns"],
)
def test_a_git_failure_logs_by_whether_the_caller_expects_it(caplog, expected_failure, warns):
    """`remote get-url origin` failing is a normal probe outcome -- _detect_forge
    returns (None, None) and the probe succeeds -- so it must not warn.
    git_read's warning stays for failures no caller handles."""
    kw = {"expected_failure": True} if expected_failure else {}
    with caplog.at_level(logging.DEBUG, logger="kraft.config"):
        assert config.git_read(Path.cwd(), "remote", "get-url", "nope", **kw) is None
    assert any(r.levelno >= logging.WARNING for r in caplog.records) is warns
    if not warns:
        assert any(r.levelno == logging.DEBUG for r in caplog.records)


# ── config file IO ──


def test_an_atomic_write_leaves_no_half_file_behind(tmp_path):
    target = tmp_path / "policy.yaml"
    config.write_yaml(target, {"default": {"attempts": 3}})
    assert yaml.safe_load(target.read_text()) == {"default": {"attempts": 3}}
    assert [p.name for p in tmp_path.iterdir()] == ["policy.yaml"]


def test_a_broken_config_file_raises_rather_than_reading_as_empty(tmp_path):
    bad = tmp_path / "repos.yaml"
    bad.write_text("repos: [not-a-mapping]")
    with pytest.raises(config.ConfigError):
        config.load_repos(bad)
    bad.write_text("{{{")
    with pytest.raises(config.ConfigError):
        config.load_repos(bad)
    assert config.load_repos(tmp_path / "missing.yaml") == []


# --- unrecognised keys (Kraft-4hn34) --------------------------------------------


def _entry(tmp_path, **extra):
    path = tmp_path / "repos.yaml"
    path.write_text(yaml.safe_dump({"repos": [{"path": "/r", "setup_command": "", **extra}]}))
    return path


def test_an_unrecognised_key_loads_with_a_warning_naming_it_and_the_repo(tmp_path, caplog):
    """Refusing every unknown key would break installs carrying retired ones,
    but none may pass silently: an operator reading the log learns it binds
    nothing."""
    with caplog.at_level(logging.WARNING, logger="kraft.config"):
        (entry,) = config.load_repos(_entry(tmp_path, legacy_widget=1), validate_steering=False)

    assert entry.path == "/r"
    assert any("legacy_widget" in r.message and "/r" in r.message for r in caplog.records)


def test_a_caller_that_reports_unrecognised_keys_itself_gets_no_warning(caplog):
    """`kraft admin doctor` retypes `GET /repos` and fails a row per key; the
    loader's own warning would say it twice, on a terminal."""
    with caplog.at_level(logging.WARNING, logger="kraft.config"):
        config.RepoEntry.model_validate(
            {"path": "/r", "legacy_widget": 1}, context={"unrecognised_keys_reported": True}
        )

    assert not caplog.records


def test_the_keys_kraft_itself_writes_are_not_unrecognised(tmp_path, caplog):
    """`name`, `enabled` and `default_chain_template` are written on connect
    and read elsewhere; they are no operator's typo."""
    with caplog.at_level(logging.WARNING, logger="kraft.config"):
        config.load_repos(
            _entry(tmp_path, name="r", enabled=True, default_chain_template="default"),
            validate_steering=False,
        )

    assert not caplog.records


@pytest.mark.parametrize(
    "typo, meant",
    [("automated_reviews", "automated_review"), ("setup_comand", "setup_command")],
)
def test_a_key_one_typo_from_a_field_is_refused_naming_the_field(tmp_path, typo, meant):
    """`automated_reviews:` would otherwise read as "no reviewer configured" --
    the one outcome Ruling 171 records as a deliberate choice."""
    with pytest.raises(config.ConfigError, match=f"did you mean '{meant}'"):
        config.load_repos(_entry(tmp_path, **{typo: {}}), validate_steering=False)


# ── workspaces (`workspace-declares-root-and-members`) ──


def _write(tmp_path, repos, workspaces):
    path = tmp_path / "repos.yaml"
    path.write_text(yaml.safe_dump({"repos": repos, "workspaces": workspaces}))
    return path


_WS_REPOS = [{"path": "/ws", "id": "ws"}, {"path": "/ws/libs/a", "id": "lib-a"}]


def test_a_workspace_is_read_from_repos_yaml_by_repository_id(tmp_path):
    """The daemon's `repos.yaml` declares workspaces in the V1 shape, beside
    the repository list: a root and each member with its mount path, both
    naming a connected repository by `id`."""
    path = _write(
        tmp_path,
        _WS_REPOS,
        {
            "ws": {
                "root": "ws",
                "root_pointer_default": "bump",
                "members": {
                    "a": {"repository": "lib-a", "path": "libs/a"},
                    # A name prefix of `libs/a`, not inside it.
                    "ab": {"repository": "lib-a", "path": "libs/ab"},
                },
            }
        },
    )
    (ws,) = config.load_workspaces(path).values()
    assert (ws.id, ws.root, ws.root_pointer_default) == ("ws", "ws", "bump")
    assert ws.members["a"].repository == "lib-a"
    assert ws.members["a"].path == "libs/a"
    assert config.load_workspaces(_write(tmp_path, _WS_REPOS, None)) == {}


@pytest.mark.parametrize(
    ("repos", "workspaces", "match"),
    [
        (_WS_REPOS, {"ws": {"root": "nope"}}, "root 'nope'"),
        (
            _WS_REPOS,
            {"ws": {"root": "ws", "members": {"a": {"repository": "gone", "path": "libs/a"}}}},
            "'gone'",
        ),
        (_WS_REPOS, {"ws": {"root": "ws", "members": {"a": {"repository": "lib-a"}}}}, "path"),
        ([{"path": "/a", "id": "x"}, {"path": "/b", "id": "x"}], None, "'x'"),
        ([{"path": "/a", "id": "Not An Id"}], None, "id"),
        (
            _WS_REPOS,
            {
                "ws": {
                    "root": "ws",
                    "members": {
                        "a": {"repository": "lib-a", "path": "libs/a"},
                        "x": {"repository": "lib-a", "path": "./libs/a/vendor/x/"},
                    },
                }
            },
            "member 'x' at 'libs/a/vendor/x' is inside member 'a' at 'libs/a'",
        ),
        (
            _WS_REPOS,
            {
                "ws": {
                    "root": "ws",
                    "members": {
                        "a": {"repository": "lib-a", "path": "libs/a"},
                        "b": {"repository": "lib-a", "path": "./libs/a/"},
                    },
                }
            },
            "member 'b' at 'libs/a' is mounted where member 'a' is",
        ),
    ],
    ids=[
        "an-unknown-root",
        "an-unknown-member-repository",
        "a-member-with-no-mount-path",
        "two-repositories-with-one-id",
        "an-id-no-reference-can-name",
        "a-member-nested-inside-another",
        "two-members-at-one-mount",
    ],
)
def test_a_workspace_that_cannot_assemble_is_refused_at_load(tmp_path, repos, workspaces, match):
    with pytest.raises(config.ConfigError, match=match):
        config.load_workspaces(_write(tmp_path, repos, workspaces))


def test_save_repos_keeps_the_workspaces_section(tmp_path):
    """Every Settings write goes through `save_repos` with the repository list
    alone; it must not drop the workspaces declared beside it."""
    workspaces = {"ws": {"root": "ws", "members": {"a": {"repository": "lib-a", "path": "libs/a"}}}}
    path = _write(tmp_path, _WS_REPOS, workspaces)
    config.save_repos(path, [r.model_dump() for r in config.load_repos(path)])
    assert yaml.safe_load(path.read_text())["workspaces"] == workspaces


def test_a_top_level_repositories_key_fails_loudly(tmp_path):
    """Kraft-iep21 (Ruling 177): `repositories:` is not read, and a file keyed
    that way used to load 0 repositories without a word -- then blame a
    workspace for naming a repository that was right there."""
    path = tmp_path / "repos.yaml"
    path.write_text("repositories:\n  api: { path: /r }\n")
    with pytest.raises(config.ConfigError, match="did you mean 'repos:'"):
        config.load_repos(path)
    with pytest.raises(config.ConfigError, match="did you mean 'repos:'"):
        config.load_workspaces(path)


def test_the_seeded_repos_yaml_connects_nothing():
    """Kraft-tsh75: a fresh install starts with no repository, not a phantom
    smoke-test one at a path that does not exist on the machine."""
    seeded = Path(__file__).resolve().parents[1] / "templates" / "repos.yaml"
    assert config.load_repos(seeded) == []
