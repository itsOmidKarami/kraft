"""Agent profiles (Kraft-ps1ao): a named model tier -- `deep`, `strong`,
`fast` -- in `harnesses.yaml`'s `profiles:`, which a library agent task selects
with `profile:` instead of spelling out `model:`/`effort:`. The model id is
spelled per provider; the task's `profile:` name is frozen with its chain and
the profile's body is read live at every launch."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError
from support.harness import entry_of, v1_chain, v1_walk, write_harness_profiles

from kraft import doctor
from kraft import harness as _harness
from kraft.adapters import agent
from kraft.api.routes import harnesses as harnesses_route
from kraft.templates.environment import HarnessProfileTable, TemplateEnvironmentError
from kraft.templates.library import TemplateLibrary, TemplateLibraryError
from kraft.templates.models import AgentTask

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "templates"
#: The rc templates as every install seeded before this change has them: the
#: library on `model:`/`effort:`, and a `harnesses.yaml` with no `profiles:`.
RC = ROOT / "tests" / "fixtures" / "templates_rc"

HARNESSES = {"claude": {"provider": "claude"}, "codex": {"provider": "codex"}}
PROFILES = {
    "deep": {"effort": "high", "model": {"claude": "opus", "codex": "gpt-5.6-sol"}},
    "fast": {"effort": "low", "model": {"claude": "haiku"}},
}


def _table(profiles, harnesses=HARNESSES) -> HarnessProfileTable:
    return HarnessProfileTable.from_mapping(
        {"harnesses": harnesses, "profiles": profiles},
        "harnesses.yaml",
        harnesses=_harness.load(None).valid,
    )


def _task(**fields) -> AgentTask:
    return AgentTask.model_validate({"id": "t", "kind": "agent", "prompt": "p", **fields})


def _live(tmp_path, monkeypatch, tasks: dict, *, profiles=PROFILES, harnesses=HARNESSES) -> Path:
    """A live templates dir: `tasks` in `library.yaml`, one chain `c` running
    each as node `<task>`, and `harnesses.yaml` with `harnesses` and `profiles`."""
    live = tmp_path / "templates"
    (live / "chains").mkdir(parents=True)
    (live / "library.yaml").write_text(yaml.safe_dump({"tasks": tasks}))
    nodes = [
        {"id": name, "kind": "exec", "tasks": [{"id": "t", "extends": name}]} for name in tasks
    ]
    (live / "chains" / "c.yaml").write_text(yaml.safe_dump({"id": "c", "nodes": nodes}))
    body = {"harnesses": harnesses} | ({"profiles": profiles} if profiles is not None else {})
    (live / "harnesses.yaml").write_text(yaml.safe_dump(body))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(live))
    return live


def _launches(templates: Path, monkeypatch) -> list[tuple]:
    """(chain, task path, harness, model, effort, permission_mode) for every
    agent task of every chain in `templates`, resolved the way a launch does."""
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    library = TemplateLibrary.from_yaml_dir(templates)
    out = []
    for cid in library.chain_ids:
        chain = library.resolve_chain(cid)
        for node in chain.nodes:
            for t in node.tasks():
                if isinstance(t.task, AgentTask):
                    inv = agent.resolve_agent_task(t.task, None, None, steering=chain.steering)
                    out.append(
                        (cid, t.path, inv.harness, inv.model, inv.effort, inv.permission_mode)
                    )
    return out


# ── the profile table ──


def test_the_shipped_harnesses_file_ships_deep_strong_and_fast():
    table = HarnessProfileTable.from_yaml(
        SHIPPED / "harnesses.yaml", harnesses=_harness.load(None).valid
    )
    profiles = table.agent_profiles
    assert set(profiles) == {"deep", "strong", "fast"}
    assert (profiles["deep"].model, profiles["deep"].effort) == (
        {"claude": "opus", "codex": "gpt-5.6-sol"},
        "high",
    )
    assert (profiles["strong"].model, profiles["strong"].effort) == (
        {"claude": "sonnet", "codex": "gpt-5.6-terra"},
        "high",
    )
    assert (profiles["fast"].model, profiles["fast"].effort) == ({"claude": "haiku"}, "low")


def test_an_absent_profiles_section_is_an_empty_table():
    """A 1.0.0 `harnesses.yaml` loads unchanged."""
    table = HarnessProfileTable.from_mapping(
        {"harnesses": HARNESSES}, "harnesses.yaml", harnesses=_harness.load(None).valid
    )
    assert table.agent_profiles == {}
    assert set(table.profiles) == {"claude", "codex"}


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({"model": {"nonesuch": "x"}}, "nonesuch"),
        ({"model": {}}, "profiles.p: model"),
        ({"effort": "high"}, "profiles.p: model"),
        ({"model": {"claude": 5}}, "profiles.p: model.claude"),
        ({"model": {"claude": "opus"}, "prompt": "be clever"}, "profiles.p: prompt"),
        ({"model": {"codex": "gpt-5.6-sol"}, "effort": "max"}, "effort 'max'"),
    ],
    ids=["unknown-provider", "empty-model", "no-model", "non-string", "extra-key", "bad-effort"],
)
def test_a_bad_profile_definition_is_refused_at_load(body, why):
    with pytest.raises(TemplateEnvironmentError, match=why):
        _table({"p": body})


def test_a_profile_id_must_be_an_identifier():
    with pytest.raises(TemplateEnvironmentError, match="'Deep'"):
        _table({"Deep": {"model": {"claude": "opus"}}})


def test_one_named_provider_accepting_the_effort_is_enough():
    """`max` is claude's and not codex's: a profile naming both still loads,
    and only a codex pairing is refused (`resolve_profile`)."""
    table = _table({"p": {"effort": "max", "model": {"claude": "opus", "codex": "gpt-5.6-sol"}}})
    assert table.agent_profiles["p"].effort == "max"


# ── one route per task ──


def test_a_task_selecting_a_profile_and_a_model_is_refused():
    for both in ({"model": "opus"}, {"effort": "high"}):
        with pytest.raises(ValidationError, match="profile"):
            _task(harness="claude", profile="deep", **both)
    assert _task(harness="claude", profile="deep").profile == "deep"
    assert _task(harness="claude", model="opus", effort="high").profile is None


def test_a_library_task_selecting_both_routes_is_refused_naming_it(tmp_path, monkeypatch):
    live = _live(
        tmp_path,
        monkeypatch,
        {
            "both": {
                "kind": "agent",
                "harness": "claude",
                "prompt": "p",
                "profile": "deep",
                "model": "opus",
            }
        },
    )
    with pytest.raises(TemplateLibraryError, match=r"both\.main\.t.*profile"):
        TemplateLibrary.from_yaml_dir(live).resolve_chain("c")


@pytest.mark.parametrize(
    ("parent", "child", "route"),
    [
        ({"model": "opus", "effort": "high"}, {"profile": "deep"}, ("deep", None, None)),
        ({"profile": "deep"}, {"model": "opus"}, (None, "opus", None)),
        ({"profile": "deep"}, {"effort": "low"}, (None, None, "low")),
        ({"model": "opus"}, {"effort": "low"}, (None, "opus", "low")),
    ],
    ids=["profile-over-fields", "model-over-profile", "effort-over-profile", "fields-merge"],
)
def test_the_nearer_layers_route_wins_whole_through_extends(
    tmp_path, monkeypatch, parent, child, route
):
    tasks = {
        "base": {"kind": "agent", "harness": "claude", "prompt": "p", **parent},
        "derived": {"extends": "base", **child},
    }
    live = _live(tmp_path, monkeypatch, tasks)
    chain = TemplateLibrary.from_yaml_dir(live).resolve_chain("c")
    task = next(t.task for n in chain.nodes if n.id == "derived" for t in n.tasks())
    assert (task.profile, task.model, task.effort) == route


def test_a_retry_override_of_the_model_displaces_the_profile():
    """A retry's `task_config` is the nearest layer, so its route wins the same way."""
    from kraft.templates.retry import validate_retry_override

    task = {"id": "t", "kind": "agent", "harness": "claude", "prompt": "p", "profile": "deep"}
    chain = v1_chain([{"id": "n", "kind": "exec", "tasks": [task]}], repo="/repo")
    fork = validate_retry_override(chain, "n.main.t", task_config={"model": "opus"}).chain
    retried = next(t.task for node in fork.chain.nodes for t in node.tasks())
    assert (retried.profile, retried.model) == (None, "opus")
    back = validate_retry_override(fork, "n.main.t", task_config={"profile": "fast"}).chain
    again = next(t.task for node in back.chain.nodes for t in node.tasks())
    assert (again.profile, again.model) == ("fast", None)


# ── pairing: checked where a task meets its harness ──


PAIRED = {
    "claude_fast": {"kind": "agent", "harness": "claude", "prompt": "p", "profile": "fast"},
    "codex_deep": {"kind": "agent", "harness": "codex", "prompt": "p", "profile": "deep"},
}
CODEX_FAST = {"kind": "agent", "harness": "codex", "prompt": "p", "profile": "fast"}


def _view(live: Path) -> dict:
    library = TemplateLibrary.from_yaml_dir(live)
    return harnesses_route._view(SimpleNamespace(templates_dir=live, library=library))


def test_a_profile_omitting_a_provider_nobody_pairs_is_no_problem(tmp_path, monkeypatch):
    view = _view(_live(tmp_path, monkeypatch, PAIRED))
    assert view["error"] is None
    listed = {p["id"]: p for p in view["agent_profiles"]}
    assert listed["fast"] == {
        "id": "fast",
        "effort": "low",
        "model": {"claude": "haiku"},
        "used_by": ["tasks.claude_fast"],
        "chains": ["c"],
        "problems": [],
    }
    assert listed["deep"]["used_by"] == ["tasks.codex_deep"]
    assert listed["deep"]["problems"] == []


def test_only_the_task_pairing_a_missing_provider_is_refused(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch, {**PAIRED, "codex_fast": CODEX_FAST})
    listed = {p["id"]: p for p in _view(live)["agent_profiles"]}
    assert listed["fast"]["problems"] == [
        "chain 'c' task 'codex_fast.main.t': profile 'fast' has no model for provider "
        "'codex' (harness 'codex')"
    ]
    assert listed["deep"]["problems"] == []


@pytest.mark.parametrize(
    ("task", "why"),
    [
        (
            {"harness": "claude", "profile": "gone"},
            "profile 'gone' is not defined in harnesses.yaml; "
            "known are ['deep', 'fast', 'maxed', 'wrong']",
        ),
        (
            {"harness": "codex", "profile": "fast"},
            "profile 'fast' has no model for provider 'codex'",
        ),
        ({"harness": "codex", "profile": "maxed"}, "provider 'codex' takes no effort 'max'"),
        ({"harness": "codex", "profile": "wrong"}, "provider 'codex' takes no model 'opus'"),
    ],
    ids=["missing", "no-provider", "effort", "model"],
)
def test_doctor_fails_a_pairing_the_launch_would_refuse(tmp_path, monkeypatch, task, why):
    profiles = {
        **PROFILES,
        "maxed": {"effort": "max", "model": {"claude": "opus", "codex": "gpt-5.6-sol"}},
        "wrong": {"model": {"claude": "opus", "codex": "opus"}},
    }
    _live(tmp_path, monkeypatch, {"x": {"kind": "agent", "prompt": "p", **task}}, profiles=profiles)
    failed = [r for r in doctor._agent_checks() if not r["ok"] and r["name"].startswith("profile")]
    assert [r["name"] for r in failed] == [f"profile: {task['profile']}"]
    assert "chain 'c' task 'x.main.t'" in failed[0]["detail"]
    assert why in failed[0]["detail"]


def test_doctor_is_quiet_about_a_clean_pairing(tmp_path, monkeypatch):
    _live(tmp_path, monkeypatch, PAIRED)
    assert not [r for r in doctor._agent_checks() if r["name"].startswith("profile")]


def test_a_harness_save_that_breaks_a_pairing_is_refused(tmp_path, monkeypatch):
    """The Settings guard on a harness save: moving `claude` onto codex would
    leave `claude_fast` with no model, so it is refused and nothing is written."""
    from fastapi import HTTPException

    live = _live(tmp_path, monkeypatch, PAIRED)
    before = (live / "harnesses.yaml").read_text()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(templates_dir=live, library=TemplateLibrary.from_yaml_dir(live))
        )
    )
    import asyncio

    with pytest.raises(HTTPException) as refused:
        asyncio.run(harnesses_route.put_harness("claude", {"provider": "codex"}, request))
    assert refused.value.status_code == 422
    assert "profile 'fast' has no model for provider 'codex'" in refused.value.detail
    assert (live / "harnesses.yaml").read_text() == before


# ── resolution: the profile is the task's own rung ──


def test_a_profile_fills_the_tasks_own_rung(tmp_path, monkeypatch):
    harnesses = {
        "claude": {"provider": "claude", "defaults": {"model": "sonnet", "effort": "medium"}}
    }
    _live(tmp_path, monkeypatch, {}, harnesses=harnesses)
    task = _task(harness="claude", profile="deep")
    repo = entry_of({"models": {"claude": "haiku"}})

    # Beats the repo's model for the harness and the harness defaults.
    inv = agent.resolve_agent_task(task, repo, None)
    assert (inv.model, inv.effort) == ("opus", "high")
    # The item's (and so the node's) override beats it.
    over = agent.resolve_agent_task(task, repo, None, item_override={"model": "x", "effort": "low"})
    assert (over.model, over.effort) == ("x", "low")
    # Escalation still wins.
    esc = agent.resolve_agent_task(
        task, repo, None, escalate=True, item_override={"escalate_model": "opus-max"}
    )
    assert esc.model == "opus-max"


def test_the_profile_body_is_read_live_and_its_name_is_frozen(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch, {})
    task = {"id": "t", "kind": "agent", "harness": "claude", "prompt": "p", "profile": "deep"}
    snapshot = v1_chain([{"id": "n", "kind": "exec", "tasks": [task]}], repo="/repo")
    frozen = type(snapshot).from_json(snapshot.to_json())
    [resolved] = [t.task for node in frozen.chain.nodes for t in node.tasks()]
    assert resolved.profile == "deep"
    assert agent.resolve_agent_task(resolved, None, None).model == "opus"

    edited = {**PROFILES, "deep": {"effort": "xhigh", "model": {"claude": "opus-next"}}}
    (live / "harnesses.yaml").write_text(
        yaml.safe_dump({"harnesses": HARNESSES, "profiles": edited})
    )
    inv = agent.resolve_agent_task(resolved, None, None)
    assert (inv.model, inv.effort) == ("opus-next", "xhigh")

    (live / "harnesses.yaml").write_text(yaml.safe_dump({"harnesses": HARNESSES}))
    with pytest.raises(agent.ProfileUnavailable, match="profile 'deep' is not defined"):
        agent.resolve_agent_task(resolved, None, None)
    assert issubclass(agent.ProfileUnavailable, agent.HarnessUnavailable)


async def test_a_profile_missing_at_launch_stops_the_task_for_a_human(tmp_path, repo, monkeypatch):
    """No substitution: the launch stops before anything runs, in the load check's words."""
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "empty-home"))
    _live(tmp_path, monkeypatch, {}, profiles={"fast": PROFILES["fast"]})
    task = {"id": "write", "kind": "agent", "harness": "codex", "prompt": "p", "profile": "fast"}

    status, _evts, sessions, _row = await v1_walk(
        tmp_path, v1_chain([{"id": "spec", "kind": "exec", "tasks": [task]}], repo=repo), repo=repo
    )

    assert status == "needs_human"
    assert [s["status"] for s in sessions] == ["config_error"]
    log = Path(sessions[0]["log_path"]).read_text()
    assert "profile 'fast' has no model for provider 'codex' (harness 'codex')" in log, log


# ── shipped defaults and the upgrade path ──

#: Every shipped agent task's resolved launch before agent profiles existed,
#: computed on release/v1 @ bd20df44. Moving the library onto `profile:` must
#: not change one of them.
BEFORE = [
    ("default", "spec.main.author", "claude", "sonnet", None, None),
    ("default", "plan.main.author", "claude", "sonnet", None, None),
    ("default", "chain_revision.main.revise", "claude", "sonnet", None, None),
    ("default", "implementation.main.implement", "claude", "sonnet", "high", None),
    ("default", "verification.review.code_review", "claude", "sonnet", None, None),
    ("default", "verification.fix_loop.main.repair", "claude", "sonnet", "high", None),
    ("default", "verification.fix_loop.judge", "claude", "sonnet", "high", None),
    ("default", "work_brief.main.author", "claude", "sonnet", None, None),
    ("default", "describe_merge_request.main.author", "claude", "sonnet", None, None),
    (
        "default",
        "merge_request_feedback.on_failure.repair.repair_feedback",
        "claude",
        "sonnet",
        "high",
        None,
    ),
    ("default", "merge_request_feedback.fix_loop.repair.repair", "claude", "sonnet", "high", None),
    ("default", "merge_request_feedback.fix_loop.judge", "claude", "sonnet", "high", None),
    ("default", "work_item_summary.main.author", "claude", "sonnet", None, None),
    ("quick-task", "implementation.main.implement", "claude", "sonnet", "high", None),
]


def test_the_shipped_library_selects_profiles_and_launches_as_before(monkeypatch):
    library = yaml.safe_load((SHIPPED / "library.yaml").read_text())["tasks"]
    on_profile = {name for name, t in library.items() if t.get("profile")}
    assert on_profile == {
        "implementer",
        "repair_verification",
        "repair_mr_feedback",
        "repair_mr_checks",
        "strict_judge",
    }
    assert {t.get("profile") for t in library.values()} == {None, "strong"}
    assert not [n for n, t in library.items() if "model" in t or "effort" in t]
    assert _launches(SHIPPED, monkeypatch) == BEFORE


def test_an_rc_home_keeps_its_files_and_launches_as_before(monkeypatch):
    """Upgrade, the common case: `templates/` is seeded once and never
    overwritten, so an rc install keeps its library on `model:`/`effort:` and
    its `harnesses.yaml` with no `profiles:`. Both still load and every task
    launches exactly as it did."""
    assert "profiles" not in yaml.safe_load((RC / "harnesses.yaml").read_text())
    assert _launches(RC, monkeypatch) == BEFORE


def test_a_fresh_seed_gets_the_library_and_its_profiles_together(tmp_path, monkeypatch):
    """`seed_home` (and a pre-V1 major update, which stages the same bundle)
    copies the directory whole, so a home that gets the shipped library also
    gets the `profiles:` it selects."""
    from kraft.cli import admin

    bundle = tmp_path / "bundle"
    shutil.copytree(SHIPPED, bundle / "templates")
    monkeypatch.setattr(admin, "BUNDLED", bundle)
    home = tmp_path / "home" / "templates"
    assert admin.seed_home(home)
    assert _launches(home, monkeypatch) == BEFORE


def test_the_shipped_library_on_an_rc_harnesses_file_is_named_not_substituted(
    tmp_path, monkeypatch
):
    """The one mixed case -- someone copies the new library over an rc home by
    hand -- stops for a human naming the profile, never runs a guessed model."""
    home = tmp_path / "templates"
    shutil.copytree(SHIPPED, home)
    shutil.copy(RC / "harnesses.yaml", home / "harnesses.yaml")
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(home))
    failed = [r for r in doctor._agent_checks() if not r["ok"]]
    assert [r["name"] for r in failed] == ["profile: strong"]
    assert "profile 'strong' is not defined in harnesses.yaml" in failed[0]["detail"]


def test_the_capability_manifest_tells_an_rc_home_about_profiles():
    from kraft import capabilities

    [entry] = [c for c in capabilities.added_since("1.0.0rc5") if c.name == "profiles"]
    assert "harnesses.yaml" in entry.how and "profile:" in entry.how


def test_write_harness_profiles_keeps_the_profiles_section(tmp_path):
    (tmp_path / "harnesses.yaml").write_text(json.dumps({"harnesses": {}, "profiles": PROFILES}))
    write_harness_profiles(tmp_path, {"claude": {"provider": "claude"}})
    assert yaml.safe_load((tmp_path / "harnesses.yaml").read_text())["profiles"] == PROFILES
