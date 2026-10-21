"""`kl.py migrate`: a directory's pre-V1 registry and unsliced run carried onto V1."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402
import kl_bindings  # noqa: E402
import kl_chains  # noqa: E402
import kl_migrate  # noqa: E402
import kl_yaml  # noqa: E402

#: This repo's own `.kraft-lite/registry.yaml`, verbatim as it stood before the
#: move to V1: every kind of entry, comments, folded prompts, a re-bound hook.
REGISTRY = """\
# Kraft Lite hook registry for this repo.
# Written by /kraft-lite:init from `kl.py detect`. Meant to be edited and committed.
# Every `skill` entry has a `prompt` sibling: if the skill is renamed or
# uninstalled the chain falls back to the instruction instead of stopping.

on.spec.requested:
  kind: skill
  skill: superpowers:brainstorming
  prompt: Agree requirements and write a spec before any code.
  # also found: (none)

on.plan.requested:
  kind: skill
  skill: superpowers:writing-plans
  prompt: Turn the approved spec into a written, step-by-step implementation plan.
  # also found: superpowers:executing-plans (the run-a-plan half, not the write-a-plan half)

on.chain.review_ready:
  kind: prompt
  prompt: >-
    Read back the whole chain - spec, plan, and the nodes still to run - and
    confirm the order and the gates still match the work before anything is built.
  # also found: (none)

on.env.prepare:
  kind: prompt
  prompt: >-
    Confirm the work is already isolated in its own worktree, and create one only
    if it is not.
  # was: kind: skill, superpowers:using-git-worktrees. Re-bound at the
  # chain_finalized gate of kl-3b54b9c4: chains started from inside a worktree
  # already have their isolation, and a second worktree orphans the state
  # directory and the uncommitted artifacts the run has produced so far.

on.implementation.start:
  kind: skill
  skill: superpowers:test-driven-development
  prompt: Implement the plan, writing the failing test before the code each time.
  # also found: (none)

on.test.run:
  kind: subprocess
  command: [just, test]
  # Was a hand-pinned file list, re-bound at every chain_finalized gate because
  # each new plan touches files the previous binding didn't know about (a
  # verify node that runs the wrong files is worse than a slow one -- it goes
  # green having tested nothing). `just test` runs pytest-testmon instead:
  # it derives the affected tests from coverage of what actually changed, so
  # it can't go stale the way a hand-picked list did, and the chain's worktree
  # persists across nodes/retries so .testmondata keeps paying off run to run.

on.review.local.run:
  kind: skill
  skill: superpowers:requesting-code-review
  prompt: Review the working tree for correctness and for anything simpler that would do.
  # also found: caveman:caveman-review, ponytail:ponytail-review,
  #             superpowers:receiving-code-review (processes feedback, does not produce it)

on.mr.open:
  kind: skill
  skill: superpowers:finishing-a-development-branch
  prompt: Decide how this branch integrates and open the merge request.
  # also found: (none)

on.ci.poll:
  kind: subprocess
  command: [gh, pr, checks]
  # Runs once and reports. Never waits - see the `next` skill.
  # Re-bound 2026-09-22 (Kraft-gzzpg): this repo moved forges mid-flight to
  # GitHub (origin is now git@github.com:itsOmidKarami/kraft.git, and there is
  # no .gitlab-ci.yml left); kl.py's own detect() already derives this from
  # origin/CI-file presence (Kraft-rje) and would now propose gh too - this
  # hand-written file just hadn't been regenerated since the move.

on.review.mr.run:
  kind: skill
  skill: superpowers:requesting-code-review
  prompt: Review the merge request as published, not just the local tree.
  # also found: caveman:caveman-review, ponytail:ponytail-review,
  #             superpowers:receiving-code-review

on.human_review.requested:
  kind: prompt
  prompt: >-
    Present the finished work to the human for sign-off: what changed, what was
    verified, and anything deliberately left out.
  # also found: (none)

on.merge:
  kind: skill
  skill: superpowers:finishing-a-development-branch
  prompt: Merge the branch and clean up the worktree.
  # also found: (none)

on.repos.scan:
  kind: prompt
  prompt: Scan the repos this chain touches for relevant context before work starts.
  # also found: (none)

on.mr.describe:
  kind: skill
  skill: mr-metadata
  prompt: Write the merge request's title and description from the finished diff.
  # also found: (none)

on.mr.rebase:
  kind: prompt
  prompt: Rebase the branch onto its target if it has drifted before merging.
  # also found: (none)

on.mr.sync:
  kind: prompt
  prompt: Pull the merge request's latest remote state into the local branch before continuing.
  # also found: (none)

on.merge.watch:
  kind: prompt
  prompt: Watch the merge until it lands or fails, then report which.
  # also found: (none)

on.slice.implementation.start:
  kind: skill
  skill: superpowers:subagent-driven-development
  prompt: >-
    Implement the slice's tasks with one subagent per task, each working test
    first. After each task run a spec-compliance review and then a code-quality
    review, with a capped fix round. Run independent tasks in parallel, each in
    its own isolated worktree. Set a model explicitly on every subagent. Stop
    after the last task's review: no final whole-branch review and no finishing
    or merge step, since on.review.local.run and open_mr are those.
  # also found: (none)

on.slice.test.run:
  kind: prompt
  prompt: >-
    Run only the test paths this slice's tasks touch, and the repo's fast checks,
    as your slice instructions list them. Never the full suite; CI runs it. List
    the paths in the PR body.
  # also found: (none)
"""

SMALL_REGISTRY = "on.spec.requested:\n  kind: prompt\n  prompt: Write the spec.\n"

#: What `REGISTRY` becomes. The two hooks V1 has no task for are dropped, and so
#: are the two slice hooks, whose bindings are the packaged slice defaults word for word.
EXPECTED_BINDINGS = {
    "tasks": {
        "spec_author": {
            "kind": "skill",
            "skill": "superpowers:brainstorming",
            "prompt": "Agree requirements and write a spec before any code.",
        },
        "plan_author": {
            "kind": "skill",
            "skill": "superpowers:writing-plans",
            "prompt": "Turn the approved spec into a written, step-by-step implementation plan.",
        },
        "revise_chain": {
            "kind": "prompt",
            "prompt": (
                "Read back the whole chain - spec, plan, and the nodes still to run - and "
                "confirm the order and the gates still match the work before anything is built."
            ),
        },
        "implementer": {
            "kind": "skill",
            "skill": "superpowers:test-driven-development",
            "prompt": "Implement the plan, writing the failing test before the code each time.",
        },
        "code_review": {
            "kind": "skill",
            "skill": "superpowers:requesting-code-review",
            "prompt": (
                "Review the working tree for correctness and for anything simpler that would do."
            ),
        },
        "describe_mr": {
            "kind": "skill",
            "skill": "mr-metadata",
            "prompt": "Write the merge request's title and description from the finished diff.",
        },
        "write_summary": {
            "kind": "prompt",
            "prompt": (
                "Present the finished work to the human for sign-off: what changed, what was "
                "verified, and anything deliberately left out."
            ),
        },
    },
    "builtins": {
        "kraft.verify_changed_test_scopes": {"kind": "subprocess", "command": ["just", "test"]},
        "kraft.mr_rebase": {
            "kind": "prompt",
            "prompt": "Rebase the branch onto its target if it has drifted before merging.",
        },
    },
    "forge": {
        "mr.open_draft": {
            "kind": "skill",
            "skill": "superpowers:finishing-a-development-branch",
            "prompt": "Decide how this branch integrates and open the merge request.",
        },
        "mr.ci": {"kind": "subprocess", "command": ["gh", "pr", "checks"]},
        "mr.automated_review": {
            "kind": "skill",
            "skill": "superpowers:requesting-code-review",
            "prompt": "Review the merge request as published, not just the local tree.",
        },
        "mr.merge": {
            "kind": "skill",
            "skill": "superpowers:finishing-a-development-branch",
            "prompt": "Merge the branch and clean up the worktree.",
        },
        "mr.sync": {
            "kind": "prompt",
            "prompt": (
                "Pull the merge request's latest remote state into the local branch before "
                "continuing."
            ),
        },
        "mr.post_merge_ci": {
            "kind": "prompt",
            "prompt": "Watch the merge until it lands or fails, then report which.",
        },
    },
}
DROPPED = [
    "on.env.prepare",
    "on.repos.scan",
    "on.slice.implementation.start",
    "on.slice.test.run",
]

TEMPLATE = "kl-aaaaaaaa"
NODES = list(kl_migrate.LEGACY_NODES)


def snapshot(cwd: Path) -> dict[str, bytes]:
    return {str(p.relative_to(cwd)): p.read_bytes() for p in sorted(cwd.rglob("*")) if p.is_file()}


def legacy_run(
    cwd: Path,
    *,
    at: str = "implementation",
    state: str = "open",
    gate: str | None = None,
    attempt: int = 0,
    note: str | None = None,
    waited: int | None = None,
    template: str = "default",
    chain_id: str = TEMPLATE,
    sliced: bool = False,
    keep_epic: bool = True,
) -> str:
    """A run as the pre-V1 plugin left it: frozen on the legacy `template`, the
    nodes before `at` closed, `at` in `state`, the rest open. Its gate node
    carries the gate it is blocked at, its attempts and any rejection note."""
    nodes = [{"id": n, "tasks": [], "gate_after": None, "fix_loop": None} for n in NODES]
    frozen = cwd / ".kraft-lite" / "chains" / f"{chain_id}.json"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text(json.dumps({"id": template, "nodes": nodes, "loops": {}}))
    epic = {
        "_type": "issue",
        "id": chain_id,
        "title": "A feature",
        "status": "closed",
        "description": f"Kraft Lite chain run of template {template!r}.",
        "labels": [f"{kl.CHAIN_LABEL}{template}"],
        "dependencies": [],
    }
    records, previous = [epic], chain_id
    for number, node in enumerate(NODES):
        status = "closed" if number < NODES.index(at) else "open"
        labels = [f"{kl.NODE_LABEL}{node}"]
        description = f"hooks: on.{node}"
        if node == at:
            status = state
            labels += [f"{kl.ATTEMPT_LABEL}{attempt}", f"{kl.SPENT_LABEL}{attempt}"]
            if gate:
                labels += [f"{kl.GATE_LABEL}{gate}", f"{kl.BLOCKED_LABEL}2026-10-20T10:00:00Z"]
            if waited is not None:
                labels.append(f"{kl.WAITED_LABEL}{waited}")
            if note:
                description += f"\n{note}"
        node_id = f"{chain_id}.{node}"
        records.append(
            {
                "_type": "issue",
                "id": node_id,
                "title": node,
                "status": status,
                "description": description,
                "labels": labels,
                "dependencies": [kl._dep(node_id, previous)],
            }
        )
        previous = node_id
    if sliced:
        records.append(
            {
                "_type": "issue",
                "id": "ksr-aaaaaaaa",
                "title": "run",
                "status": "open",
                "labels": [f"{kl.RUN_LABEL}{chain_id}", f"{kl.MODE_LABEL}feature"],
                "dependencies": [],
            }
        )
    kl.append_jsonl(cwd / ".kraft-lite" / "chain.jsonl", records if keep_epic else records[1:])
    return chain_id


def stored(cwd: Path) -> dict[str, dict]:
    return {r["id"]: r for r in kl.read_jsonl(cwd / ".kraft-lite" / "chain.jsonl")}


def walk_of(records: dict[str, dict], chain_id: str) -> list[dict]:
    """The chain's node records in walk order."""
    mine = [r for r in records.values() if r["id"].split(".")[0] == chain_id]
    return [r for r in kl.ordered(mine) if kl.label_value(r, kl.NODE_LABEL)]


def test_registry_becomes_bindings(kl_cli, tmp_path):
    (tmp_path / ".kraft-lite").mkdir()
    (tmp_path / ".kraft-lite" / "registry.yaml").write_text(REGISTRY)
    # Read the same with or without PyYAML, which a bare Python 3.10 has not.
    assert kl_yaml.load(REGISTRY) == kl_yaml.read(tmp_path / ".kraft-lite" / "registry.yaml")

    result = kl_cli(tmp_path, "migrate")

    assert kl_bindings.read_bindings(tmp_path) == EXPECTED_BINDINGS
    assert result == {"bindings": {**EXPECTED_BINDINGS, "dropped": DROPPED}, "chains": []}
    assert not (tmp_path / ".kraft-lite" / "registry.yaml").exists()
    assert (tmp_path / ".kraft-lite" / "registry.yaml.migrated").read_text() == REGISTRY
    # The file is plain YAML: the subset reader and a full one agree on it.
    text = (tmp_path / ".kraft-lite" / "bindings.yaml").read_text()
    assert kl_yaml.load(text) == EXPECTED_BINDINGS
    # And the walk takes it: every task is dispatched by what the registry bound.
    assert kl_cli(tmp_path, "start", "--title", "t")["chain_id"]
    state = kl_cli(tmp_path, "state")
    assert state["node"] == "spec"
    # (The skill is not installed here, so it falls back to its prompt.)
    [task] = state["steps"][0]["tasks"]
    assert task["source"].startswith("bindings:tasks.spec_author")
    assert task["prompt"].startswith("Agree requirements")


def test_legacy_tables_name_real_targets():
    bundled = kl_chains.resolve_bundled()
    assert [n for ids in kl_migrate.LEGACY_NODES.values() for n in ids] == [
        n["id"] for n in bundled["nodes"]
    ]
    components = kl_chains.bundled_library().components["tasks"]
    for hook, target in kl_migrate.LEGACY_HOOKS.items():
        if target is None:
            continue
        section, key = target
        known = {
            "tasks": components,
            "builtins": kl_chains.BUILTIN_REFS,
            "forge": kl_chains.FORGE_TARGETS,
            "slice.tasks": ("implementer",),
            "slice.builtins": kl_chains.BUILTIN_REFS,
        }[section]
        assert key in known, hook


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({}, id="empty"),
        pytest.param(
            {"tasks": {"x": {"kind": "prompt", "prompt": "plain words, with: a colon"}}}, id="prose"
        ),
        pytest.param(
            {
                "tasks": {
                    "x": {
                        "kind": "prompt",
                        "prompt": "on",
                        "a": "yes",
                        "b": "null",
                        "c": "1.5",
                        "d": "12",
                        "e": "",
                        "f": "~",
                    }
                }
            },
            id="strings_that_read_as_something_else",
        ),
        pytest.param(
            {"k": {"a": "# not a comment", "b": "- not a list", "c": " padded ", "d": "x: y"}},
            id="indicators",
        ),
        pytest.param(
            {
                "k": {
                    "a": 'say "hi"',
                    "b": "back\\slash",
                    "c": "two\nlines",
                    "d": "caf\u00e9 \u2014",
                }
            },
            id="quotes_escapes_unicode",
        ),
        pytest.param(
            {
                "builtins": {
                    "kraft.mr_rebase": {
                        "kind": "subprocess",
                        "command": ["git", "-C", "a b", "--x=1", "on"],
                    }
                }
            },
            id="command_flow_list",
        ),
        pytest.param(
            {"slice": {"tasks": {"implementer": {"kind": "skip", "why": "n/a"}}}}, id="nested"
        ),
        pytest.param({"k": {"flag": True, "none": None, "n": 3}}, id="non_strings"),
    ],
)
def test_emitted_yaml_reads_back(data):
    assert kl_yaml.load(kl_migrate.emit_yaml(data)) == data
    assert kl_yaml.load(kl_migrate.HEADER + kl_migrate.emit_yaml(data)) == data


@pytest.mark.parametrize(
    "legacy, expected",
    [
        pytest.param(
            {"at": "verify", "attempt": 2},
            {"node": "verification", "status": "open", "kind": "exec", "attempt": 2, "note": ""},
            id="open_at_verify",
        ),
        pytest.param(
            {"at": "open_mr"},
            {"node": "work_brief", "status": "open", "kind": "exec", "attempt": 0, "note": ""},
            id="open_at_open_mr",
        ),
        pytest.param(
            {"at": "spec", "state": "blocked", "gate": "spec_approval", "waited": 30},
            {
                "node": "spec_approval",
                "status": "blocked",
                "kind": "gate",
                "attempt": 0,
                "note": "",
            },
            id="blocked_at_spec_gate",
        ),
        pytest.param(
            {"at": "chain_review", "state": "blocked", "gate": "chain_finalized"},
            {
                "node": "chain_revision_approval",
                "status": "blocked",
                "kind": "gate",
                "attempt": 0,
                "note": "",
            },
            id="blocked_at_chain_finalized",
        ),
        pytest.param(
            {"at": "human_review", "state": "blocked", "gate": "human_review_approval"},
            {"node": "final_review", "status": "blocked", "kind": "gate", "attempt": 0, "note": ""},
            id="blocked_at_human_review",
        ),
        # Blocked where V1 has no gate to hold it: it runs again, the wait it had banked.
        pytest.param(
            {"at": "verify", "state": "blocked", "gate": "ask", "waited": 30},
            {"node": "verification", "status": "open", "kind": "exec", "attempt": 0, "note": ""},
            id="blocked_where_v1_has_no_gate",
        ),
        # Blocked ad hoc at a gate its node does not have: nothing was done for the
        # work_brief yet, so it runs, rather than being marked done behind a gate.
        pytest.param(
            {"at": "open_mr", "state": "blocked", "gate": "ad_hoc", "waited": 30},
            {"node": "work_brief", "status": "open", "kind": "exec", "attempt": 0, "note": ""},
            id="blocked_ad_hoc_at_open_mr",
        ),
        pytest.param(
            {"at": "spec", "state": "blocked", "gate": "plan_approval"},
            {"node": "spec", "status": "open", "kind": "exec", "attempt": 0, "note": ""},
            id="blocked_at_another_nodes_gate",
        ),
        pytest.param(
            {"at": "implementation", "attempt": 1, "note": "Rejected: tests are flaky"},
            {
                "node": "implementation",
                "status": "open",
                "kind": "exec",
                "attempt": 1,
                "note": "Rejected: tests are flaky",
            },
            id="rejected_note_kept",
        ),
    ],
)
def test_legacy_run_carried(legacy, expected, kl_cli, tmp_path):
    chain_id = legacy_run(tmp_path, **legacy)

    result = kl_cli(tmp_path, "migrate")

    [moved] = result["chains"]
    new_id = moved["to"]
    assert moved == {"from": chain_id, "to": new_id} and new_id != chain_id
    assert "bundled" in result["note"]
    state = kl_cli(tmp_path, "state", "--chain-id", new_id)
    assert {key: state[key] for key in expected} == expected
    # Everything before the current node is closed, it and everything after are not.
    records = stored(tmp_path)
    walk = walk_of(records, new_id)
    ids = [kl.label_value(r, kl.NODE_LABEL) for r in walk]
    assert ids == [n["id"] for n in kl_chains.resolve_bundled()["nodes"]]
    at = ids.index(expected["node"])
    assert [r["status"] for r in walk[:at]] == ["closed"] * at
    assert [r["status"] for r in walk[at:]][0] == expected["status"]
    assert all(r["status"] == "open" for r in walk[at + 1 :])
    assert records[new_id]["title"] == "A feature" and records[new_id]["status"] == "closed"
    if expected["status"] == "blocked":
        gate = walk[at]
        assert kl.label_value(gate, kl.GATE_LABEL) == expected["node"]
        assert kl.label_value(gate, kl.BLOCKED_LABEL) == "2026-10-20T10:00:00Z"
        assert kl.label_value(gate, kl.WAITED_LABEL) == (
            str(legacy["waited"]) if "waited" in legacy else None
        )
    elif "waited" in legacy:
        held = walk[at]
        assert kl.label_value(held, kl.BLOCKED_LABEL) is None
        assert int(kl.label_value(held, kl.WAITED_LABEL)) >= legacy["waited"]
    # The new run is frozen on the bundled default, and the old one is closed for good.
    assert json.loads(kl._frozen_path(tmp_path, new_id).read_text()) == kl_chains.resolve_bundled()
    old = [r for r in records.values() if r["id"].split(".")[0] == chain_id]
    assert all(r["status"] == "closed" for r in old)
    # Only the records that were open are written: those closed already stay as they were.
    legacy_at = NODES.index(legacy.get("at", "implementation"))
    labelled = [
        kl.label_value(records[f"{chain_id}.{node}"], kl_migrate.MIGRATED_LABEL) for node in NODES
    ]
    assert labelled == [None] * legacy_at + [new_id] * (len(NODES) - legacy_at)
    assert [c["status"] for c in kl_cli(tmp_path, "chains") if c["chain_id"] == chain_id] == [
        "done"
    ]


@pytest.mark.parametrize(
    "legacy, named",
    [
        pytest.param({"sliced": True}, f"{TEMPLATE} (it is sliced)", id="sliced"),
        pytest.param(
            {"keep_epic": False},
            f"{TEMPLATE} (its chain record is missing)",
            id="no_epic",
        ),
        pytest.param(
            {"template": "slice"}, f"{TEMPLATE} (it is a slice's child chain)", id="slice_child"
        ),
        pytest.param(
            {"template": "short"},
            f"{TEMPLATE} (it runs the chain 'short', not the default)",
            id="custom_chain",
        ),
    ],
)
def test_sliced_or_custom_legacy_refused(legacy, named, kl_cli, tmp_path):
    (tmp_path / ".kraft-lite").mkdir()
    (tmp_path / ".kraft-lite" / "registry.yaml").write_text(SMALL_REGISTRY)
    legacy_run(tmp_path, **legacy)
    # A second, plain run beside it: nothing of it is moved either.
    legacy_run(tmp_path, chain_id="kl-bbbbbbbb")
    before = snapshot(tmp_path)

    refused = kl_cli(tmp_path, "migrate", ok=False)

    assert "finish it on the previous Kraft Lite version, or start again" in refused.stderr
    assert named in refused.stderr and "Traceback" not in refused.stderr
    assert "kl-bbbbbbbb" not in refused.stderr
    assert snapshot(tmp_path) == before


def test_other_verbs_refuse_a_legacy_run_until_it_is_migrated(kl_cli, tmp_path):
    legacy_run(tmp_path)
    assert "python3 kl.py migrate" in kl_cli(tmp_path, "state", ok=False).stderr

    new_id = kl_cli(tmp_path, "migrate")["chains"][0]["to"]

    assert kl_cli(tmp_path, "state")["chain_id"] == new_id


def test_migrate_twice_changes_nothing(kl_cli, tmp_path):
    (tmp_path / ".kraft-lite").mkdir()
    (tmp_path / ".kraft-lite" / "registry.yaml").write_text(REGISTRY)
    legacy_run(tmp_path, at="verify", attempt=1)
    first = kl_cli(tmp_path, "migrate")
    assert first["chains"] and first["bindings"]["dropped"] == DROPPED
    before = snapshot(tmp_path)

    again = kl_cli(tmp_path, "migrate")

    assert again == {"bindings": {}, "chains": []}
    assert snapshot(tmp_path) == before


def test_bindings_exist_refused(kl_cli, tmp_path):
    (tmp_path / ".kraft-lite").mkdir()
    (tmp_path / ".kraft-lite" / "registry.yaml").write_text(REGISTRY)
    (tmp_path / ".kraft-lite" / "bindings.yaml").write_text("tasks: {}\n")
    legacy_run(tmp_path)
    before = snapshot(tmp_path)

    refused = kl_cli(tmp_path, "migrate", ok=False)

    assert "bindings.yaml and .kraft-lite/registry.yaml both exist" in refused.stderr
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "registry, named",
    [
        pytest.param(
            SMALL_REGISTRY + "on.deploy.staging:\n  kind: prompt\n  prompt: Deploy.\n",
            "hooks that no V1 task replaces: on.deploy.staging",
            id="custom_hook",
        ),
        pytest.param(
            "on.spec.requested:\n  kind: skill\n  skill: a:b\n",
            "tasks.spec_author.prompt: a skill binding needs a prompt",
            id="skill_without_prompt",
        ),
    ],
)
def test_a_registry_that_cannot_be_carried_is_refused_whole(registry, named, kl_cli, tmp_path):
    (tmp_path / ".kraft-lite").mkdir()
    (tmp_path / ".kraft-lite" / "registry.yaml").write_text(registry)
    legacy_run(tmp_path)
    before = snapshot(tmp_path)

    refused = kl_cli(tmp_path, "migrate", ok=False)

    assert named in refused.stderr and "Nothing was migrated" in refused.stderr
    assert "Traceback" not in refused.stderr
    assert snapshot(tmp_path) == before


def test_a_migrated_run_walks_the_bundled_default_not_the_users(kl_cli, tmp_path, monkeypatch):
    user = tmp_path / "user-config"
    shutil.copytree(kl_chains.BUNDLED, user)
    quick = (kl_chains.BUNDLED / "chains" / "quick-task.yaml").read_text()
    (user / "chains" / "default.yaml").write_text(quick.replace("id: quick-task", "id: default"))
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(user))
    work = tmp_path / "work"
    work.mkdir()
    legacy_run(work, at="verify")
    assert kl_chains.resolve(work, {"KRAFT_CONFIG_DIR": str(user)}, "default")["source"] == "user"

    new_id = kl_cli(work, "migrate")["chains"][0]["to"]

    frozen = json.loads(kl._frozen_path(work, new_id).read_text())
    assert frozen == kl_chains.resolve_bundled() and frozen["source"] == "bundled"


def test_the_single_chain_json_of_an_older_plugin_is_legacy_too(kl_cli, tmp_path):
    chain_id = legacy_run(tmp_path, at="plan")
    frozen = kl._frozen_path(tmp_path, chain_id)
    frozen.rename(tmp_path / ".kraft-lite" / "chain.json")

    new_id = kl_cli(tmp_path, "migrate")["chains"][0]["to"]

    assert kl_cli(tmp_path, "state", "--chain-id", new_id)["node"] == "plan"


def test_a_v1_run_is_left_alone(walk, kl_cli, tmp_path):
    walk.start(tmp_path)
    legacy = legacy_run(tmp_path, chain_id="kl-bbbbbbbb", at="plan")
    before = stored(tmp_path)

    moved = kl_cli(tmp_path, "migrate")["chains"]

    assert [m["from"] for m in moved] == [legacy]
    after = stored(tmp_path)
    assert all(after[i] == r for i, r in before.items() if not i.startswith(legacy))


def test_an_interrupted_migrate_is_finished_by_running_it_again(kl_cli, tmp_path):
    done, interrupted = tmp_path / "done", tmp_path / "interrupted"
    for cwd in (done, interrupted):
        (cwd / ".kraft-lite").mkdir(parents=True)
        (cwd / ".kraft-lite" / "registry.yaml").write_text(REGISTRY)
    expected = kl_cli(done, "migrate")
    # As a crash between writing the bindings and renaming the registry leaves it.
    (interrupted / ".kraft-lite" / "bindings.yaml").write_text(
        (done / ".kraft-lite" / "bindings.yaml").read_text()
    )

    resumed = kl_cli(interrupted, "migrate")

    assert resumed == expected
    assert snapshot(interrupted) == snapshot(done)
    assert not (interrupted / ".kraft-lite" / "registry.yaml").exists()
