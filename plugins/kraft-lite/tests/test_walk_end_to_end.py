"""Walks of Kraft's V1 chains through the real CLI, in a subprocess, in a temp
repo: gate nodes, steps and their dispatch, fix-loop caps, restarts, attachments.
The unit tests cover the rules; this covers the thing the human sees."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import kl

PLUGIN = Path(__file__).resolve().parents[1]


def repo_chain(root: Path, name: str, text: str) -> str:
    """A chain in `root`'s repo library, as `start --chain` names it."""
    chains = root / ".kraft" / "chains"
    chains.mkdir(parents=True, exist_ok=True)
    (chains / f"{name}.yaml").write_text(textwrap.dedent(text))
    return f"repo:{name}"


def stored(root: Path) -> dict[str, dict]:
    return {r["id"]: r for r in kl.read_jsonl(root / kl.STATE_DIR / "chain.jsonl")}


def test_a_whole_chain_walks_to_done(kl_cli, walk, default_chain, tmp_path):
    kl_cli(tmp_path, "start", "--title", "Add a flag")

    for node in default_chain["nodes"]:
        state = kl_cli(tmp_path, "state")
        assert (state["node"], state["kind"]) == (node["id"], node["kind"])
        if node["kind"] == "gate":
            kl_cli(tmp_path, "gate")
            assert kl_cli(tmp_path, "state")["status"] == "blocked"
            kl_cli(tmp_path, "approve")
        else:
            assert [s["id"] for s in state["steps"]] == [s["id"] for s in node["steps"]]
            walk.produce(tmp_path, state)
            kl_cli(tmp_path, "close")

    assert kl_cli(tmp_path, "state")["status"] == "done"
    # `state` is the only other verb allowed to run once the walk is over, and
    # the summary is worth nothing if it is refused at exactly the moment it is
    # wanted.
    out = kl_cli(tmp_path, "summary")
    assert out["status"] == "done"
    assert out["title"] == "Add a flag"
    assert [n["node"] for n in out["nodes"]] == [n["id"] for n in default_chain["nodes"]]
    assert out["totals"]["closed"] == len(default_chain["nodes"])
    assert out["duration_seconds"] >= 0


def test_state_reports_steps_and_dispatch(walk, kl_cli, tmp_path):
    walk.start(tmp_path)
    walk.to(tmp_path, "verification")

    state = kl_cli(tmp_path, "state")
    assert [s["id"] for s in state["steps"]] == ["tests", "review"]
    [tests] = state["steps"][0]["tasks"]
    assert (tests["path"], tests["kind"]) == ("verification.tests.test_changed_scopes", "prompt")
    assert tests["source"] == "default:builtins.kraft.verify_changed_test_scopes"
    assert state["fix_loop"]["judge"]["path"] == "verification.fix_loop.judge"
    assert [s["id"] for s in state["fix_loop"]["steps"]] == ["main"]
    # Kraft's methods are named by path, never inlined: `state` dispatches every task.
    [review] = state["steps"][1]["tasks"]
    assert review["source"] == "default:agent (shipped method)"
    assert str((PLUGIN / "methods" / "code-review.md").resolve()) in review["prompt"]
    for name in ("code-review", "fix-loop-judge"):
        body = (PLUGIN / "methods" / f"{name}.md").read_text().split("\n# ", 1)[1]
        assert json.dumps(body)[1:-1] not in json.dumps(state)  # both JSON-escaped

    bindings = tmp_path / ".kraft-lite" / "bindings.yaml"
    bindings.write_text(
        "builtins:\n  kraft.verify_changed_test_scopes:\n"
        "    kind: subprocess\n    command: [just, test]\n"
    )
    [tests] = kl_cli(tmp_path, "state")["steps"][0]["tasks"]
    assert (tests["kind"], tests["command"]) == ("subprocess", ["just", "test"])
    assert tests["source"] == "bindings:builtins.kraft.verify_changed_test_scopes"


def test_state_reports_a_gate_node(walk, kl_cli, tmp_path):
    walk.start(tmp_path)
    kl_cli(tmp_path, "produced", "--kind", "spec", "--path", "docs/old.md")
    kl_cli(tmp_path, "produced", "--kind", "spec", "--path", "docs/spec.md")
    refused = kl_cli(tmp_path, "gate", ok=False)
    assert "spec is an exec node, not a gate node" in refused.stderr
    # `approve` answers a gate; on an exec node it would close it with nothing run.
    refused = kl_cli(tmp_path, "approve", ok=False)
    assert "spec is an exec node, not a gate node: run its tasks and close it" in refused.stderr
    kl_cli(tmp_path, "close")
    # Approve closes a gate; `close` does not.
    refused = kl_cli(tmp_path, "close", ok=False)
    assert "kraft-lite: spec_approval is a gate: approve or reject it" in refused.stderr

    state = kl_cli(tmp_path, "state")

    assert (state["node"], state["kind"], state["steps"]) == ("spec_approval", "gate", [])
    assert state["produced"] == {"spec": "docs/spec.md"}, "the newest of each kind, for any task"
    assert state["gate"] == {
        "message": "Review and approve the specification.",
        "artifact": "spec",
        "artifact_path": "docs/spec.md",
        "reject_to": "spec",
        "chain_finalized": False,
        "artifact_required": False,
        "slice_gate": False,
        "reviewer": None,
        "review": None,
    }
    [spec] = [r for r in stored(tmp_path).values() if r["title"] == "spec"]
    assert [x for x in spec["labels"] if x.startswith(kl.ARTIFACT_LABEL)] == [
        "kraft-artifact:spec=docs/spec.md"
    ]


@pytest.mark.parametrize(
    "verb",
    [
        pytest.param(["produced", "--kind", "spec", "--path", "spec.md "], id="produced"),
        pytest.param(["start", "--title", "t", "--spec", "spec.md "], id="start_spec"),
    ],
)
def test_a_path_bd_could_not_unlabel_is_refused(walk, verb, kl_cli, tmp_path):
    """bd's label removal trims its argument, so a label value ending in
    whitespace would stay on the record forever (Kraft-6tf4c)."""
    walk.start(tmp_path)
    (tmp_path / "spec.md ").write_text("# spec\n")
    frozen = tmp_path / kl.STATE_DIR / "chains"
    before = (stored(tmp_path), sorted(frozen.iterdir()))

    refused = kl_cli(tmp_path, *verb, ok=False)

    assert "kraft-artifact:spec='spec.md ' starts or ends with whitespace" in refused.stderr
    assert (stored(tmp_path), sorted(frozen.iterdir())) == before


#: A chain whose gate names no `reject_to`, after two exec nodes.
UNAIMED = """\
    nodes:
      - {id: first, kind: exec, tasks: [{id: t, kind: agent, prompt: One.}]}
      - {id: second, kind: exec, tasks: [{id: t, kind: agent, prompt: Two.}]}
      - {id: check, kind: gate}
      - {id: after, kind: exec, tasks: [{id: t, kind: agent, prompt: Three.}]}
    """


@pytest.mark.parametrize(
    ("chain", "gate", "flags", "reopened"),
    [
        pytest.param("default", "local_review", [], "implementation", id="reject_to"),
        pytest.param(
            "default",
            "local_review",
            ["--from-node", "verification"],
            "verification",
            id="from_node_overrides",
        ),
        pytest.param("unaimed", "check", [], "second", id="nearest_exec_node"),
    ],
)
def test_reject_goes_to_reject_to(walk, kl_cli, tmp_path, chain, gate, flags, reopened):
    ref = repo_chain(tmp_path, "unaimed", UNAIMED) if chain == "unaimed" else "default"
    kl_cli(tmp_path, "start", "--title", "t", "--chain", ref)
    walk.to(tmp_path, gate)
    kl_cli(tmp_path, "gate")

    state = kl_cli(tmp_path, "reject", "--note", "not like this", *flags)

    assert (state["node"], state["status"]) == (reopened, "open")
    assert state["note"] == "Rejected: not like this"
    rows = kl_cli(tmp_path, "summary")["nodes"]
    at = [n["node"] for n in rows].index(reopened)
    assert {n["status"] for n in rows[at:]} == {"open"}
    assert {n["status"] for n in rows[:at]} == {"closed"}


#: A gate on `notes`, which `a` writes; `%s` is its `artifact_required`.
NOTES_GATE = """\
    nodes:
      - {id: a, kind: exec, tasks: [{id: w, kind: agent, prompt: Write notes., produces: notes}]}
      - {id: g, kind: gate, message: Read the notes., artifact: notes, artifact_required: %s}
      - {id: b, kind: exec, tasks: [{id: t, kind: agent, prompt: Go on.}]}
    """


@pytest.mark.parametrize(
    ("chain", "gate", "missing"),
    [
        pytest.param("default", "final_review", "the final review document", id="final_gate"),
        pytest.param("true", "g", "the required document 'notes'", id="artifact_required"),
        pytest.param("false", "g", None, id="not_required"),
    ],
)
def test_approve_refuses_a_gate_without_its_document(walk, kl_cli, tmp_path, chain, gate, missing):
    """As Kraft's approve: the final gate, or one that requires its document,
    is never approved with nothing to read."""
    ref = repo_chain(tmp_path, "notes", NOTES_GATE % chain) if chain != "default" else chain
    kl_cli(tmp_path, "start", "--title", "t", "--chain", ref)
    walk.to(tmp_path, gate)  # every document recorded on the way
    document = tmp_path / kl_cli(tmp_path, "gate")["gate"]["artifact_path"]
    document.unlink()

    if missing:
        refused = kl_cli(tmp_path, "approve", ok=False)
        assert f"{gate}: {missing} is missing" in refused.stderr
        assert kl_cli(tmp_path, "state")["status"] == "blocked"
        document.write_text("# Back\n")
    assert kl_cli(tmp_path, "approve")["node"] != gate


NO_CHANGE = '```json\n{"rationale": "The chain fits."}\n```\n'


@pytest.mark.parametrize(
    ("body", "rationale"),
    [
        pytest.param(NO_CHANGE, "The chain fits.", id="no_change"),
        pytest.param(
            '---\nkind: x\n---\n```json\n{"rationale": "Fits.", "skip": [], "overrides": {}}\n```',
            "Fits.",
            id="empty_changes_under_front_matter",
        ),
        pytest.param(
            '```json\n{"rationale": "Skip.", "skip": [{"node": "verification"}]}\n```\n',
            None,
            id="a_change",
        ),
        pytest.param("The chain fits; no change.\n", None, id="prose"),
        pytest.param(
            '```json\n{"rationale": "Fits.", "unslice": "s"}\n```\n', None, id="other_key"
        ),
        pytest.param('```json\n{"rationale": ""}\n```\n', None, id="no_rationale"),
        pytest.param("proposal", None, id="slicing_proposed"),
        # As Kraft's strict read: a key twice, a symlink, a file outside the repo, past 1 MB.
        pytest.param(
            '```json\n{"rationale": "Fits.", "add": [{"after": "x"}], "add": []}\n```',
            None,
            id="duplicate_key",
        ),
        pytest.param("symlink", None, id="behind_a_symlink"),
        pytest.param("outside", None, id="outside_the_repo"),
        pytest.param("too_big", None, id="past_the_size_cap"),
    ],
)
def test_an_unchanged_chain_revision_passes_without_asking(walk, kl_cli, tmp_path, body, rationale):
    """As Kraft's walk: a change set that changes nothing, with no slicing to
    ask about, passes its gate and says why; anything else asks."""
    chain_id = walk.to_chain_revision(tmp_path)
    if body == "proposal":
        (tmp_path / kl.STATE_DIR / "proposals").mkdir()
        (tmp_path / kl.STATE_DIR / "proposals" / f"{chain_id}.json").write_text("{}")
    special = {"too_big": NO_CHANGE + " " * kl.REVISION_MAX_BYTES}
    special |= dict.fromkeys(("proposal", "symlink", "outside"), NO_CHANGE)
    outside = tmp_path.parent / f"{tmp_path.name}-revision.md"
    revision = outside if body == "outside" else tmp_path / "revision.md"
    revision.write_text(special.get(body, body))
    kl_cli(tmp_path, "produced", "--kind", "chain_revision", "--path", str(revision))
    if body == "symlink":  # swapped after it was recorded, as Kraft's read guards against
        (tmp_path / "real.md").write_text(NO_CHANGE)
        revision.unlink()
        revision.symlink_to(tmp_path / "real.md")
    kl_cli(tmp_path, "close")

    state = kl_cli(tmp_path, "gate")

    if rationale is None:
        assert (state["node"], state["status"]) == ("chain_revision_approval", "blocked")
        assert "passed_unchanged" not in state
    else:
        assert (state["node"], state["status"]) == ("implementation", "open")
        assert state["passed_unchanged"] == {
            "node": "chain_revision_approval",
            "rationale": rationale,
        }


def test_attachment_trim(kl_cli, default_chain, tmp_path):
    (tmp_path / "spec.md").write_text("# Spec\n")
    (tmp_path / "plan.md").write_text("# Plan\n")

    started = kl_cli(tmp_path, "start", "--title", "t", "--spec", "spec.md", "--plan", "plan.md")

    frozen = json.loads(kl._frozen_path(tmp_path, started["chain_id"]).read_text())
    dropped = ("spec", "spec_approval", "plan", "plan_approval")
    assert [n["id"] for n in frozen["nodes"]] == [
        n["id"] for n in default_chain["nodes"] if n["id"] not in dropped
    ]
    assert "final_review" in [n["id"] for n in frozen["nodes"]]
    epic = stored(tmp_path)[started["chain_id"]]
    assert {"kraft-artifact:spec=spec.md", "kraft-artifact:plan=plan.md"} <= set(epic["labels"])
    assert kl_cli(tmp_path, "state")["node"] == "chain_revision"
    missing = kl_cli(tmp_path, "start", "--title", "t", "--spec", "nope.md", ok=False)
    assert "--spec nope.md: no such file" in missing.stderr


#: A chain whose fix loop declares no `max_attempts`.
UNCAPPED = """\
    nodes:
      - id: build
        kind: exec
        tasks: [{id: t, kind: agent, prompt: Build.}]
        fix_loop:
          tasks: [{id: fix, kind: agent, prompt: Fix.}]
    """


#: Chain files whose fix-loop cap comes from somewhere else, by file name: the
#: loop's own `max_attempts`, else the node policy's, else Kraft's default.
CAPPED = {
    "uncapped.yaml": UNCAPPED,
    "node_policy.yaml": UNCAPPED.replace(
        "        fix_loop:\n", "        policy: {max_attempts: 5}\n        fix_loop:\n"
    ),
    "loop_and_policy.yaml": UNCAPPED.replace(
        "        fix_loop:\n",
        "        policy: {max_attempts: 5}\n        fix_loop:\n          max_attempts: 2\n",
    ),
}


@pytest.mark.parametrize(
    ("chain", "node", "cap"),
    [
        pytest.param("default", "verification", 2, id="declared_max_attempts"),
        pytest.param("uncapped.yaml", "build", 3, id="default_cap"),
        pytest.param("node_policy.yaml", "build", 5, id="node_policy_max_attempts"),
        pytest.param("loop_and_policy.yaml", "build", 2, id="loop_beats_node_policy"),
    ],
)
def test_fix_loop_cap(walk, kl_cli, tmp_path, chain, node, cap):
    if chain != "default":
        (tmp_path / chain).write_text(textwrap.dedent(CAPPED[chain]))
    kl_cli(tmp_path, "start", "--title", "t", "--chain", chain)
    walk.to(tmp_path, node)
    assert kl_cli(tmp_path, "state")["cap"] == cap

    over = [kl_cli(tmp_path, "attempt")["over_cap"] for _ in range(cap + 1)]

    assert over == [False] * cap + [True]


@pytest.mark.parametrize(
    ("flags", "key", "node", "target", "gate", "repair", "note", "count", "cap"),
    [
        pytest.param(
            [],
            "on_base_changed",
            "merge_request_feedback",
            "verification",
            "local_review",
            [],
            "Base changed: restarted from verification",
            "restarts",
            "is over its restart cap: it restarted 3 times (cap 3)",
            id="base_changed",
        ),
        # The merge train or queue dropped the PR: after the repair, the head goes
        # back through the PR's checks and the final gate before it is offered again.
        pytest.param(
            ["--merge-rejected"],
            "on_merge_rejected",
            "merge",
            "merge_request_feedback",
            "final_review",
            ["merge.on_merge_rejected.repair.main.repair"],
            "Merge rejected: restarted from merge_request_feedback",
            "rejections",
            "is over its merge-rejection cap: its merge was rejected 3 times (cap 3)",
            id="merge_rejected",
        ),
    ],
)
def test_restart_from(
    walk, kl_cli, tmp_path, flags, key, node, target, gate, repair, note, count, cap
):
    walk.start(tmp_path)
    refused = kl_cli(tmp_path, "restart", *flags, ok=False)
    assert f"spec has no {key} to restart from" in refused.stderr
    walk.to(tmp_path, node)
    kl_cli(tmp_path, "attempt")
    policy = kl_cli(tmp_path, "state")[key]
    steps = (policy.get("repair") or {"steps": []})["steps"]
    assert [t["path"] for s in steps for t in s["tasks"]] == repair
    for times in (1, 2, 3):
        state = kl_cli(tmp_path, "restart", *flags)

        assert (state["node"], state["attempt"]) == (target, 0)
        assert state["note"].endswith(note)
        [reopened] = [r for r in stored(tmp_path).values() if r["id"].endswith(f".{gate}")]
        assert reopened["status"] == "open", "a gate in the span is answered again"
        walk.to(tmp_path, node)
        state = kl_cli(tmp_path, "state")
        assert state["attempt"] == 0
        assert state[key] == {**policy, count: times}

    assert policy["cap"] == 3
    refused = kl_cli(tmp_path, "restart", *flags, ok=False)
    assert f"kraft-lite: {node} {cap}. Stop and ask the human, as over_cap" in refused.stderr
    assert kl_cli(tmp_path, "summary")["totals"]["rejections"] == 0, "a restart is no rejection"


@pytest.mark.parametrize(
    "rewind",
    [
        pytest.param(["--from-node", "verification"], id="rewind"),
        # Reopened with a note, the node starts a new round too.
        pytest.param([], id="plain_reject"),
    ],
)
def test_on_failure_ran_label_survives_state_and_clears_on_rewind(walk, kl_cli, tmp_path, rewind):
    walk.start(tmp_path)
    walk.to(tmp_path, "verification")
    assert "has no on_failure" in kl_cli(tmp_path, "mark", "--on-failure-ran", ok=False).stderr
    walk.to(tmp_path, "merge_request_feedback")
    assert kl_cli(tmp_path, "state")["on_failure_ran"] is False

    kl_cli(tmp_path, "mark", "--on-failure-ran")

    assert kl_cli(tmp_path, "state")["on_failure_ran"] is True
    assert kl_cli(tmp_path, "state")["on_failure_ran"] is True
    kl_cli(tmp_path, "reject", "--note", "again", *rewind)
    walk.to(tmp_path, "merge_request_feedback")
    assert kl_cli(tmp_path, "state")["on_failure_ran"] is False


def test_a_started_run_ignores_later_library_edits(kl_cli, tmp_path):
    one = "nodes:\n  - {id: %s, kind: exec, tasks: [{id: t, kind: agent, prompt: Go.}]}\n"
    ref = repo_chain(tmp_path, "mine", one % "before")
    kl_cli(tmp_path, "start", "--title", "t", "--chain", ref)

    repo_chain(tmp_path, "mine", one % "after")

    assert kl_cli(tmp_path, "state")["node"] == "before"


def test_chains_available_lists_every_library(kl_cli, tmp_path):
    repo_chain(tmp_path, "mine", UNCAPPED)

    found = {c["id"]: c["source"] for c in kl_cli(tmp_path, "chains", "--available")}

    assert found == {"repo:mine": "repo", "default": "bundled", "quick-task": "bundled"}


#: What a run or a registry from before V1 is refused with, by where it lies.
LEGACY = {
    "per_chain": "kraft-lite: chain {chain_id} was started on the pre-V1 format. "
    "Run: python3 kl.py migrate",
    "registry": "kraft-lite: .kraft-lite/registry.yaml is the pre-V1 hook registry. "
    "Run: python3 kl.py migrate",
}
LEGACY["pre_per_chain_file"] = LEGACY["per_chain"]


@pytest.mark.parametrize(
    ("where", "verb"),
    [
        # Every verb reads the frozen chain through one loader and passes one
        # registry check, so a verb each; `start` has its own check beside an
        # unfinished legacy run, and `approve` is a verb that writes.
        *(
            pytest.param(where, ["state"], id=f"{where}-state")
            for where in ("per_chain", "pre_per_chain_file", "registry")
        ),
        *(
            pytest.param(where, ["start", "--title", "again"], id=f"{where}-start")
            for where in ("per_chain", "pre_per_chain_file", "registry")
        ),
        pytest.param("registry", ["approve"], id="registry-approve"),
    ],
)
def test_legacy_frozen_chain_is_refused(kl_cli, tmp_path, where, verb):
    chain_id = kl_cli(tmp_path, "start", "--title", "t")["chain_id"]
    frozen = kl._frozen_path(tmp_path, chain_id)
    legacy = {"id": "default", "nodes": [{"id": "spec", "tasks": [], "gate_after": None}]}
    if where == "registry":
        (tmp_path / ".kraft-lite" / "registry.yaml").write_text("on.test.run:\n  kind: prompt\n")
    else:
        frozen.write_text(json.dumps(legacy))
    if where == "pre_per_chain_file":
        frozen.rename(tmp_path / kl.STATE_DIR / "chain.json")

    refused = kl_cli(tmp_path, *verb, ok=False)

    assert LEGACY[where].format(chain_id=chain_id) in refused.stderr
    assert kl_cli(tmp_path, "chains")[0]["chain_id"] == chain_id
    # `init` reads what `detect` finds, registry or not.
    assert "test_command" in kl_cli(tmp_path, "detect")


def test_a_frozen_chain_lacking_what_the_walk_reads_is_refused(kl_cli, tmp_path):
    """A hand-edited frozen chain is checked as an imported one is: a refusal
    naming the gap, not a `KeyError` in the middle of a verb. One flaw, for the
    wiring: the catalog is `test_chain_flaw.py`."""
    chain_id = kl_cli(tmp_path, "start", "--title", "t")["chain_id"]
    frozen = kl._frozen_path(tmp_path, chain_id)
    chain = json.loads(frozen.read_text())
    next(n for n in chain["nodes"] if n["id"] == "verification").pop("fix_loop")
    frozen.write_text(json.dumps(chain))

    refused = kl_cli(tmp_path, "state", ok=False)

    assert (
        f"{frozen} is not a usable V1 chain: exec node 'verification' has no fix_loop"
        in refused.stderr
    )
    assert "Traceback" not in refused.stderr


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({}, id="nothing"),
        pytest.param(
            {"registry.yaml": "on.test.run:\n  kind: prompt\n", "bindings.yaml": "tasks: {}\n"},
            id="registry_beside_bindings",
        ),
    ],
)
def test_start_without_bindings_runs(kl_cli, tmp_path, files):
    (tmp_path / ".kraft-lite").mkdir()
    for name, text in files.items():
        (tmp_path / ".kraft-lite" / name).write_text(text)
    started = kl_cli(tmp_path, "start", "--title", "t")

    assert started["backend"] == "jsonl"
    # Which chain it froze, and from where, so the start skill can say so.
    assert (started["chain"], started["source"]) == ("default", "bundled")
    assert kl_cli(tmp_path, "state")["node"] == "spec"


def test_start_refuses_a_bindings_file_it_cannot_use(kl_cli, tmp_path):
    """Found at `start`, not mid-walk once the earlier nodes have run."""
    (tmp_path / ".kraft-lite").mkdir()
    (tmp_path / ".kraft-lite" / "bindings.yaml").write_text("tasks:\n  spec_author: {kind: nope}\n")

    refused = kl_cli(tmp_path, "start", "--title", "t", ok=False)

    assert "bindings.yaml: tasks.spec_author.kind: unknown kind 'nope'" in refused.stderr
    assert not (tmp_path / kl.STATE_DIR / "chain.jsonl").exists()


def test_the_state_file_is_bd_importable(kl_cli, tmp_path):
    kl_cli(tmp_path, "start", "--title", "Add a flag")
    kl_cli(tmp_path, "close")

    records = list(stored(tmp_path).values())
    assert records, "no state was written"
    for record in records:
        assert record["_type"] == "issue"
        assert set(record) >= {"id", "title", "status", "labels", "dependencies"}


@pytest.mark.parametrize(
    "env",
    [
        pytest.param({}, id="plain"),
        # Python 3.11+ then leaves the script's own directory off `sys.path`.
        pytest.param({"PYTHONSAFEPATH": "1"}, id="safe_path"),
    ],
)
def test_kl_py_finds_its_sibling_modules(tmp_path, env):
    result = subprocess.run(
        [sys.executable, str(PLUGIN / "kl.py"), "chains", "--available"],
        cwd=tmp_path,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "default" in {chain["id"] for chain in json.loads(result.stdout)}


def test_nothing_in_the_plugin_reaches_outside_the_plugin():
    """This directory is published as its own repo. A path that climbs out of it
    passes here and breaks the moment somebody clones the public one."""
    offenders = []
    for path in PLUGIN.rglob("*.py"):
        # This file names the patterns it looks for, so it always matches itself.
        if path == Path(__file__).resolve():
            continue
        text = path.read_text()
        if "parents[2]" in text or "../.." in text:
            offenders.append(str(path.relative_to(PLUGIN)))
    assert not offenders, f"reaches above the plugin root: {offenders}"


def at_spec_gate(kl_cli, cwd: Path) -> None:
    kl_cli(cwd, "start", "--title", "Add a flag")
    kl_cli(cwd, "close")
    kl_cli(cwd, "gate")


def test_the_gate_verb_records_the_wait_it_opened(kl_cli, tmp_path):
    """The wait is only measurable if the block writes a stamp of its own: the
    approve overwrites the node's `updated_at`, so afterwards there is none."""
    at_spec_gate(kl_cli, tmp_path)
    kl_cli(tmp_path, "approve")

    node = kl_cli(tmp_path, "summary")["nodes"][1]
    assert node["blocked_seconds"] is not None
    assert node["blocked_seconds"] <= node["seconds"]


def test_no_node_waits_longer_than_it_took(walk, kl_cli, tmp_path):
    """`updated_at` is bd's import guard, not a clock: it is pushed forward when
    two writes land in the same second, so timing a node with it while timing the
    wait with the wall clock made a sub-second run report eight seconds of
    waiting, on nodes that reported taking none."""
    walk.start(tmp_path)
    walk.finish(tmp_path)

    out = kl_cli(tmp_path, "summary")
    for node in out["nodes"]:
        assert node["blocked_seconds"] is None or node["blocked_seconds"] <= node["seconds"], node
    assert out["totals"]["blocked_seconds"] <= out["duration_seconds"], out["totals"]


def test_a_chain_stopped_at_a_gate_reports_what_it_is_waiting_on(kl_cli, tmp_path):
    """The mid-run question the status skill points `summary` at: this node has
    been sitting on a human since some point, and that is the number they want."""
    at_spec_gate(kl_cli, tmp_path)

    node = kl_cli(tmp_path, "summary")["nodes"][1]
    assert node["status"] == "blocked"
    assert node["blocked_seconds"] is not None, "a gate held right now is still a wait"


def test_a_rewind_banks_the_wait_it_interrupts(kl_cli, tmp_path):
    """`reject --from-node` is how the *last* gate sends work back, so the wait it
    closes is the one a human has been sitting on longest in the whole run."""
    at_spec_gate(kl_cli, tmp_path)
    kl_cli(tmp_path, "reject", "--note", "redo", "--from-node", "spec")

    node = kl_cli(tmp_path, "summary")["nodes"][1]
    assert node["blocked_seconds"] is not None, "the gate held someone, then was rewound"


REVIEWED_CHAIN = """\
nodes:
  - {id: build, kind: exec, tasks: [{id: t, kind: agent, prompt: Go.}]}
  - id: check
    kind: gate
    message: Approve the build.
    artifact: plan
    auto_review_mode: %s
    auto_review: {id: reviewer, extends: gate_reviewer}
"""


def reviewed_chain(root: Path, mode: str) -> str:
    """A chain file, so its `extends` resolve against the bundled library."""
    path = root / "reviewed.yaml"
    path.write_text(REVIEWED_CHAIN % mode)
    return str(path)


def at_reviewed_gate(kl_cli, root: Path, mode: str) -> dict:
    """A reviewed chain started with its reviewer bound, and walked to its gate:
    what `start` printed."""
    (root / ".kraft-lite").mkdir()
    (root / ".kraft-lite" / "bindings.yaml").write_text(
        "tasks:\n  gate_reviewer: {kind: prompt, prompt: Check it.}\n"
    )
    started = kl_cli(root, "start", "--title", "t", "--chain", reviewed_chain(root, mode))
    kl_cli(root, "close")
    kl_cli(root, "gate")
    return started


def test_a_checks_first_reviewer_runs_once_per_round(kl_cli, tmp_path):
    started = at_reviewed_gate(kl_cli, tmp_path, "checks_first")

    assert started["unused_bindings"] == [], "the reviewer is a task bindings reach"
    gate = kl_cli(tmp_path, "state")["gate"]
    assert gate["review"] is None
    reviewer = gate["reviewer"]
    assert (reviewer["path"], reviewer["source"]) == (
        "check.auto_review",
        "bindings:tasks.gate_reviewer",
    )

    kl_cli(tmp_path, "mark", "--reviewed", str(tmp_path / "r.md"))
    assert kl_cli(tmp_path, "state")["gate"]["review"] == "r.md"

    kl_cli(tmp_path, "reject", "--note", "Reviewer: redo it")
    kl_cli(tmp_path, "close")
    kl_cli(tmp_path, "gate")
    assert kl_cli(tmp_path, "state")["gate"]["review"] is None, "the redone work is reviewed again"


def test_a_reviewer_that_decides_is_read_past(kl_cli, tmp_path):
    """Lite never lets an agent clear a gate."""
    started = at_reviewed_gate(kl_cli, tmp_path, "decides")

    assert started["unused_bindings"] == ["tasks.gate_reviewer"]
    assert kl_cli(tmp_path, "state")["gate"]["reviewer"] is None
    refused = kl_cli(tmp_path, "mark", "--reviewed", "r.md", ok=False)
    assert "has no checks-first reviewer" in refused.stderr


def test_a_gate_frozen_before_reviewers_still_walks(kl_cli, tmp_path):
    ref = reviewed_chain(tmp_path, "checks_first")
    kl_cli(tmp_path, "start", "--title", "t", "--chain", ref)
    [frozen] = (tmp_path / kl.STATE_DIR / "chains").glob("*.json")
    chain = json.loads(frozen.read_text())
    for node in chain["nodes"]:
        node.pop("auto_review", None)
        node.pop("auto_review_mode", None)
    frozen.write_text(json.dumps(chain))

    kl_cli(tmp_path, "close")
    kl_cli(tmp_path, "gate")

    assert kl_cli(tmp_path, "state")["gate"]["reviewer"] is None
