"""The resolve result: every schema error with its path and line, the last
valid model under a YAML error, the change list, impact, and the comment
warning."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from kraft.drafts import authored, resolve
from kraft.policy import InstancePolicy, InstancePolicyInput
from kraft.templates.library import TemplateLibrary

CHAIN = "chains/scratch.yaml"
SCRATCH = """id: scratch
nodes:
  - id: run
    kind: exec
    tasks:
      - {id: t, kind: subprocess, command: "true"}
"""


@pytest.fixture
def st(templates_dir, database):
    return SimpleNamespace(
        templates_dir=templates_dir,
        library=TemplateLibrary.from_yaml_dir(templates_dir),
        instance_policy=None,
        skills_dir=None,
        db=database,
    )


def scratch(st, text, *, published=None, **kw):
    return resolve.resolve(st, "chains", "scratch", {CHAIN: text}, {CHAIN: published}, **kw)


async def test_every_schema_error_is_a_problem_with_its_path_and_line(st):
    text = SCRATCH.replace('command: "true"}', 'command: "true", bogus: 1}') + (
        "  - {id: gate, kind: gate, message: 3}\n"
    )
    problems = scratch(st, text)["problems"]
    assert [(p["path"], p["field"], p["file"], p["line"]) for p in problems] == [
        ("run.main.t", "bogus", CHAIN, 6),
        ("gate", "message", CHAIN, 7),
    ]


async def test_a_chain_level_edit_is_a_change_and_a_new_chains_blank_description_is_not(st):
    edited = SCRATCH.replace("id: scratch\n", "id: scratch\ndescription: Runs the thing.\n")
    assert scratch(st, edited, published=SCRATCH)["changes"] == [
        {"path": "", "kind": "change", "summary": "description", "fields": ["description"]}
    ]
    assert scratch(st, SCRATCH, published=SCRATCH)["changes"] == []
    blank = SCRATCH.replace("id: scratch\n", 'id: scratch\ndescription: ""\n')
    assert scratch(st, blank, published=SCRATCH)["changes"] == []


async def test_a_yaml_error_keeps_the_newest_model_that_parses(st):
    good = SCRATCH.replace("id: run", "id: kept")
    history = [{"files": {CHAIN: good}}, {"files": {CHAIN: "nodes: [\n"}}]
    result = scratch(st, "id: scratch\nnodes:\n  - {id: [\n", published=SCRATCH, history=history)
    assert result["model"][CHAIN] == authored.load(good)
    assert result["yaml_error"]["file"] == CHAIN
    assert (result["yaml_error"]["line"], result["yaml_error"]["col"]) == (4, 1)


def chain(*nodes):
    return {"nodes": list(nodes)}


def node(id, *tasks, **fields):
    return {"id": id, "kind": "exec", "tasks": list(tasks), **fields}


def test_changes_are_adds_field_changes_removes_and_moves():
    t = {"id": "t", "kind": "agent"}
    before = chain(node("a", t), node("b"), node("c", t), node("gone", t, {"id": "u"}))
    after = chain(
        node("c", {**t, "model": "opus"}, retry="x"),
        node("a", t, {"id": "new"}),
        node("b"),
    )
    got = resolve.changes(resolve._flat(before), resolve._flat(after))
    assert got == [
        {"path": "c", "kind": "change", "summary": "retry, moved", "fields": ["retry"]},
        {"path": "c.main.t", "kind": "change", "summary": "model", "fields": ["model"]},
        {"path": "a.main.new", "kind": "add", "summary": "added"},
        # its tasks fold into the removed node
        {"path": "gone", "kind": "remove", "summary": "removed"},
    ]


async def test_impact_counts_running_items_and_the_repos_that_default_to_the_chain(st):
    def insert(c):
        for wid, template, status in [
            ("w1", "quick-task", "active"),
            ("w2", "quick-task", "completed"),
            ("w3", None, "paused"),  # no chain named runs `default`
        ]:
            c.execute(
                "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
                "status, created_at, updated_at) VALUES (?, 't', '/r', ?, '{}', ?, 'now', 'now')",
                (wid, template, status),
            )

    await st.db.write(insert)
    (st.templates_dir / "repos.yaml").write_text(
        "repos:\n  - {path: /a, default_chain_template: quick-task}\n  - {path: /b}\n"
    )
    assert resolve._impact(st, "quick-task") == {"running": 1, "repos": ["/a"]}
    assert resolve._impact(st, "default") == {"running": 1, "repos": ["/b"]}


@pytest.mark.parametrize(
    ("published", "serialized", "warned"),
    [
        ("# mine\n" + SCRATCH, [CHAIN], True),
        (SCRATCH.replace("id: run", "id: run  # mine"), [CHAIN], True),
        ("# mine\n" + SCRATCH, [], False),
        (SCRATCH, [CHAIN], False),
    ],
    ids=["serialized-comment", "trailing-comment", "typed", "no-comment"],
)
async def test_a_serialized_file_warns_when_its_published_text_has_a_comment(
    st, published, serialized, warned
):
    result = scratch(st, SCRATCH, published=published, serialized=serialized)
    assert bool(result["warnings"]) is warned


async def test_sources_name_the_layer_each_value_comes_from(st):
    st.instance_policy = InstancePolicy.from_input(
        InstancePolicyInput.model_validate({"defaults": {"tasks": {"budget_usd": 2}}})
    )
    name = "chains/default.yaml"
    files = resolve.published(st.templates_dir, [name])
    own = files[name].replace(
        "extends: spec_author\n", "extends: spec_author\n        prompt: Mine.\n"
    )
    sources = resolve.resolve(st, "chains", "default", {name: own}, files)["sources"]
    task = sources["implementation.main.implement"]
    assert sources["spec.main.author"]["prompt"] == {"value": "Mine.", "source": "chain"}
    assert sources["spec_approval"]["reject_to"] == {"value": "spec", "source": "chain"}
    assert task["harness"] == {"value": "claude", "source": "library:tasks.implementer"}
    assert task["policy.time_cap_minutes"] == {"value": 120, "source": "library:tasks.implementer"}
    assert task["policy.budget_usd"] == {"value": 2, "source": "policy"}
    assert task["model"] == {"value": None, "source": "default"}
    # The nearest ancestor that sets it: the library node, not the judge's own base.
    assert sources["verification.fix_loop.judge"]["id"]["source"] == "library:nodes.verification"
    assert sources["verification.fix_loop.judge"]["skill"]["source"] == "library:tasks.strict_judge"


AGENT_ONLY = {
    "steering",
    "policy.allowed_harnesses",
    "policy.token_budget",
    "policy.budget_usd",
    "policy.allowed_tools",
    "policy.deny_tools",
    "policy.grants",
}


@pytest.mark.parametrize(
    ("task", "own"),
    [
        ('kind: subprocess, command: "true"', {"command"}),
        ("kind: builtin, ref: kraft.mr_rebase", {"ref", "execution"}),
        ("kind: forge, target: mr.sync", {"target", "wait"}),
    ],
    ids=["subprocess", "builtin", "forge"],
)
async def test_a_task_that_runs_no_agent_lists_no_agent_only_field(st, task, own):
    text = SCRATCH.replace('kind: subprocess, command: "true"', task)
    fields = scratch(st, text)["sources"]["run.main.t"]
    assert set(fields) == {
        "id",
        "kind",
        "scope",
        "skippable",
        "icon",
        *own,
        "policy.sandbox",
        "policy.time_cap_minutes",
        "policy.total_time_cap_minutes",
    }


async def test_an_agent_only_field_a_task_sets_anyway_is_still_listed(st):
    text = SCRATCH.replace('command: "true"}', 'command: "true", policy: {budget_usd: 3}}')
    fields = scratch(st, text)["sources"]["run.main.t"]
    assert fields["policy.budget_usd"] == {"value": 3, "source": "chain"}
    assert AGENT_ONLY - set(fields) == AGENT_ONLY - {"policy.budget_usd"}


async def test_a_steering_a_task_sets_anyway_is_still_listed(st):
    text = SCRATCH.replace('command: "true"}', 'command: "true", steering: [project-standards]}')
    fields = scratch(st, text)["sources"]["run.main.t"]
    assert fields["steering"] == {"value": ["project-standards"], "source": "chain"}


async def test_a_task_with_a_recovery_lists_the_caps_its_repair_agent_runs_under(st):
    # The recovery runs under the task's own policy scope, so its caps bind
    # the repair agent: listed, and marked as the recovery's. Steering is not.
    recovery = "on_failure: {tasks: [{id: fix, kind: agent, harness: claude, prompt: Fix.}]}"
    text = SCRATCH.replace('command: "true"}', f'command: "true", {recovery}}}')
    result = scratch(st, text)
    assert result["problems"] == []
    fields = result["sources"]["run.main.t"]
    assert "steering" not in fields
    caps = AGENT_ONLY - {"steering"}
    assert caps <= set(fields)
    assert {k for k, v in fields.items() if v.get("recovery")} == caps


async def test_an_agent_task_lists_every_agent_only_field(st):
    text = SCRATCH.replace(
        'kind: subprocess, command: "true"', "kind: agent, harness: claude, prompt: Go."
    )
    fields = scratch(st, text)["sources"]["run.main.t"]
    assert AGENT_ONLY <= set(fields)
    assert not any(v.get("recovery") for v in fields.values())


@pytest.mark.parametrize(
    ("policy", "values"),
    [
        (None, {"auto_escalate_delay_s": 0, "auto_review_attempts": 1}),
        (
            SimpleNamespace(auto_escalate_delay_s=30, auto_review_attempts=2),
            {"auto_escalate_delay_s": 30, "auto_review_attempts": 2},
        ),
    ],
    ids=["defaults", "configured"],
)
async def test_policy_values_are_the_instances(st, policy, values):
    st.policy = policy
    assert scratch(st, SCRATCH)["policy_values"] == values


# ── the library ──

LIBRARY = "library.yaml"


def library(st, *replacements):
    published = resolve.published(st.templates_dir, [LIBRARY])
    text = published[LIBRARY]
    for old, new in replacements:
        assert re.search(old, text)
        text = re.sub(old, new, text)
    return resolve.resolve(st, "library", "library", {LIBRARY: text}, published)


async def test_a_library_edit_that_breaks_a_chain_names_it_its_path_and_the_component(st):
    [p] = library(st, ("skill: kraft:code-review", "skill: kraft:no-such"))["problems"]
    assert (p["chain"], p["repo"], p["path"], p["component"], p["file"]) == (
        "default",
        None,
        "verification.review.code_review",
        "tasks.code_review",
        "chains/default.yaml",
    )


async def test_a_library_edit_that_breaks_a_repo_names_the_repo(st):
    (st.templates_dir / "repos.yaml").write_text(
        "repos:\n  - {path: /b}\n  - {path: /a, steering: [project-standards]}\n"
    )
    problems = library(st, ("  project-standards:", "  standards:"))["problems"]
    [p] = [p for p in problems if p["repo"] is not None]
    assert (p["repo"], p["path"], p["component"], p["file"], p["line"]) == (
        "/a",
        "steering",
        "steering.project-standards",
        "repos.yaml",
        3,
    )


async def test_library_changes_carry_the_chains_each_reaches(st):
    (st.templates_dir / "repos.yaml").write_text(
        "repos:\n  - {path: /a, steering: [project-standards]}\n  - {path: /b}\n"
    )
    result = library(
        st,
        ("Keep changes focused.", "Stay focused."),
        ("prompt: Implement the approved plan.", "prompt: Implement it."),
        # The verification node's own `code_review` task, at its indent.
        (r"( +)extends: code_review\n", r"\1extends: code_review\n\1prompt: Look harder.\n"),
        ("  describe_mr:", "  describe:"),
    )
    assert [(c["path"], c["reaches"]) for c in result["changes"]] == [
        ("steering.project-standards", ["default"]),
        ("tasks.implementer", ["default", "quick-task"]),
        ("tasks.describe", []),
        ("nodes.verification.review.code_review", ["default"]),
        # What the published library reached.
        ("tasks.describe_mr", ["default"]),
    ]
    assert result["impact"] == {"chains": ["default", "quick-task"], "repos": ["/a"]}
