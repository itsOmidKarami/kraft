"""`executor.prompts`: the notes an agent instruction is assembled from.

That `dispatch_node` puts them in the prompt, in order, is test_dispatch's."""

import json

import pytest
from support.harness import IN_PLACE, commit_all, entry_of, git, v1_named_chain, v1_task, write
from support.store_fixtures import mk_item

from kraft import item_paths, store
from kraft.executor import prompts
from kraft.findings import Finding, JobRef
from kraft.templates.models import AgentTask


def _agent_task_model(skill: str | None = None) -> AgentTask:
    """An agent task; `skill` set means it states its own method."""
    return AgentTask.model_validate(
        v1_task("t", kind="agent", harness="claude", prompt="p", skill=skill)
    )


WORKER = _agent_task_model()
REVIEWER = _agent_task_model("kraft:code-review")

#: Where a worker can run `kraft` (host, a sandbox with `network:`) and where not.
DOCKER = {"kind": "docker", "image": "x"}
REACHABLE = pytest.mark.parametrize(
    ("sandbox", "reachable"),
    [
        (None, True),
        ({**DOCKER, "network": {"runtime": {"allow": ["x.io"]}}}, True),
        (DOCKER, False),
    ],
    ids=["host", "network", "no-network"],
)


def test_attachment_note_lists_each_document_and_keeps_the_imperative_for_the_implementer():
    out = prompts.attachment_note(
        [
            {"kind": "spec", "path": ".engineering/specs/a.md"},
            {"kind": "plan", "path": ".engineering/plans/a.md"},
        ]
    )
    assert "Spec: .engineering/specs/a.md" in out
    assert "Plan: .engineering/plans/a.md" in out
    assert "Follow the documents above" in out
    assert "Do not re-plan." in out


def test_attachment_note_drops_the_imperative_when_the_hook_has_its_own_method():
    out = prompts.attachment_note([{"kind": "plan", "path": "p.md"}], method_is_own=True)
    assert "Plan: p.md" in out  # the paths still reach the hook
    assert "Follow the documents above" not in out
    assert "Do not re-plan" not in out


def test_attachment_note_is_empty_either_way_with_no_attachments():
    assert prompts.attachment_note([]) == ""
    assert prompts.attachment_note([], method_is_own=True) == ""


def test_the_bead_note_tells_the_worker_not_to_close_beads():
    """A worker at implementation time has verify, review and merge still
    ahead of it; closing the work item's own tracking bead there says the
    work is done before it is (Kraft-a03). Kraft closes it itself at chain
    completion (`beads.complete` in `kraft.executor.walk.run_once`) -- the
    instruction has to tell the worker to leave every bead, including its
    own, alone. That every instruction ends with this note is test_dispatch's
    `test_dispatch_without_a_description_sends_the_title_alone`."""
    assert "Do not run `bd close` on any bead" in prompts.BEAD_NOTE
    assert "Kraft closes it automatically" in prompts.BEAD_NOTE


def test_method_note_names_the_section_not_a_position():
    # The instruction is sent twice -- as the `-p` argv and inside the system
    # prompt -- and `## Method` is appended only to the second. "below" would
    # point at nothing in the argv copy.
    assert "## Method" in prompts.METHOD_NOTE
    assert "below" not in prompts.METHOD_NOTE.lower()


def test_method_note_does_not_forbid_verifying():
    # Review hooks are asked by carried_findings_note to decide whether a past
    # finding is still present, which is verification.
    lowered = prompts.METHOD_NOTE.lower()
    assert "verify" not in lowered and "judge" not in lowered


def test_the_implementer_has_no_skill_so_its_brief_stays_the_task(tmp_path):
    """The implementer framing keys off `skill:`. If someone gives the shipped
    implementer a skill, every fix round silently becomes "implementing is
    another node's job" -- addressed to the node that implements."""
    implementer = v1_named_chain(tmp_path / "templates").nodes[0].steps[0].tasks[0].task
    assert implementer.skill is None


# -- steer_prefix: who a steer's note is attributed to ----------------------------


def test_a_steered_node_with_no_artifact_yet_keeps_the_plain_steer_prompt(tmp_path, run_dirs):
    """The branch fires on the document's existence, not on any gate name: a
    node whose artifact was never written has nothing to revise."""
    with_artifact = prompts.steer_prefix(
        "plan", {"id": "w1"}, tmp_path, "go left", run_dirs=run_dirs
    )
    without = prompts.steer_prefix(None, {"id": "w1"}, tmp_path, "go left", run_dirs=run_dirs)

    assert with_artifact == without == "A human has steered this run: go left\n\n"


@pytest.mark.parametrize(
    ("source", "present", "absent"),
    [
        # Kraft-bol: a rejected plan cost a full re-plan because the dispatch
        # never mentioned the document the agent had already written.
        ("human", ["read {plan} and", "evise", "A human read"], ["A human has steered"]),
        # Kraft's own recap of the last review's unresolved findings
        # (Kraft-7sec) names no author it does not have.
        ("seeded", ["no human"], ["A human has steered", "A human read"]),
        # Kraft-s7c04.6: a gate reviewer's `fixed` note, over commits that agent
        # just made in this worktree -- the re-run must not tell the human they
        # fixed it themselves.
        (
            "gate_review",
            ["No human wrote it", "may have committed changes in this worktree itself"],
            ["A human has steered", "A human read"],
        ),
    ],
    ids=["a-humans-steer-over-an-artifact-is-a-revision", "kraft-seeded", "gate-reviewer"],
)
def test_steer_prefix_names_the_notes_author(tmp_path, run_dirs, source, present, absent):
    """Both human templates name an author: `_STEER_PROMPT` says "A human has
    steered this run", and over an existing artifact `_REVISE_PROMPT` says "A
    human read ... and sent it back with this note". A note Kraft or an agent
    wrote gets neither."""
    plan = write(item_paths.docs_dir(run_dirs, "w1"), "plan.md", "# the plan\n")
    note = "Findings the last review of this node left unresolved:\n- [important] a.py:1 — x"

    # A human is the default: every caller that passes no source still means one.
    kwargs = {} if source == "human" else {"source": source}
    out = prompts.steer_prefix("plan", {"id": "w1"}, tmp_path, note, run_dirs=run_dirs, **kwargs)

    assert note in out
    assert all(text.format(plan=plan) in out for text in present), out
    assert not any(text in out for text in absent), out


# -- scope_note -------------------------------------------------------------------


_SCOPES = entry_of(
    {
        "test_scopes": [
            {"paths": ["frontend/**"], "command": "just test-ui"},
            {"paths": ["frontend/**"], "command": "just e2e-ci"},
            {"paths": ["src/**", "tests/**"], "command": "just ci-test"},
        ]
    }
)


def test_scope_note_tells_the_implementer_to_run_its_own_tests_and_not_the_suite():
    out = prompts.scope_note(_agent_task_model(), _SCOPES)
    assert "the files you touched" in out and "Do not run the full test suite" in out
    assert "just ci-test" not in out  # a listed scope command reads as one to run


def test_scope_note_is_empty_for_any_other_hook():
    """V1 keys this on the task stating a method of its own (a `skill:`), not
    on a hook name: every task with a skill is "any other hook" now."""
    for skill in ("kraft:mr-description", "kraft:code-review", "kraft:spec"):
        assert prompts.scope_note(_agent_task_model(skill), _SCOPES) == ""


def test_scope_note_is_empty_with_no_scopes_configured():
    assert prompts.scope_note(_agent_task_model(), entry_of({})) == ""
    assert prompts.scope_note(_agent_task_model(), None) == ""


def test_scope_note_handles_a_legacy_bare_test_command():
    # config.load_repos wraps a bare test_command into a ["**"] scope, but a
    # LaunchContext built by hand may not have been through that.
    out = prompts.scope_note(_agent_task_model(), entry_of({"test_command": "just test"}))
    assert "the files you touched" in out


def test_format_findings_renders_job_refs():
    found = [
        Finding(
            severity="critical",
            message="on.test.run failed: 2 job(s)",
            file=None,
            line=None,
            source_plugin="on.test.run",
            jobs=(
                JobRef(label="just test-ui", log_ref="kraft view logs w1 --session s1"),
                JobRef(label="just e2e-ci", log_ref="kraft view logs w1 --session s2"),
            ),
        )
    ]
    text = prompts.format_findings(found, repeats=set())
    assert "just test-ui → kraft view logs w1 --session s1" in text
    assert "just e2e-ci → kraft view logs w1 --session s2" in text


# -- rebase_drift_note (Kraft-4bgg) ---------------------------------------------------


def _upstream_moved(repo, files: dict[str, str], message: str):
    """Commit `files` in `repo`; returns (old_base, new_base)."""
    old_base = git(repo, "rev-parse", "HEAD")
    for name, text in files.items():
        write(repo, name, text)
    return old_base, commit_all(repo, message)


def test_rebase_drift_note_names_commits_and_files(repo):
    old_base, new_base = _upstream_moved(repo, {"moved.txt": "moved on\n"}, "moved on upstream")

    note = prompts.rebase_drift_note(repo, "kraft/some-branch", old_base, new_base)

    assert "kraft/some-branch" in note
    assert "moved on upstream" in note
    assert "moved.txt" in note


def test_rebase_drift_note_truncates_a_long_diff(repo):
    files = {f"file_{i}.txt": f"content {i}\n" * 20 for i in range(200)}
    old_base, new_base = _upstream_moved(repo, files, "a very large upstream change")

    note = prompts.rebase_drift_note(repo, "kraft/some-branch", old_base, new_base)

    assert len(note) < prompts._REBASE_NOTE_MAX + 500  # template text plus the capped body
    assert "(truncated)" in note


@REACHABLE
def test_a_worker_reports_progress_by_kraft_only_where_it_can_run_it(
    monkeypatch, sandbox, reachable
):
    """A sandbox without `network:` has no route to Kraft: the commit tag,
    which Kraft reads when no report was made, is all it is asked for."""
    monkeypatch.setattr(prompts._progress, "tasks_for", lambda *a, **k: ["a", "b"])
    note = prompts.progress_note(WORKER, {"id": "w1"}, "/wt", sandbox=sandbox, run_dirs=None)
    assert "This plan has 2 tasks." in note and "`(task K)`" in note
    assert ("kraft item progress" in note) is reachable


async def test_an_in_place_progress_note_counts_the_plan_on_its_branch(item_on, run_dirs, repo):
    """An escalation's or a reply's prompt is built before the take, while
    the checkout may be on the person's branch with their copy of the plan."""
    it = await item_on(v1_named_chain(run_dirs.base / "t"), "implementation", target=IN_PLACE)
    git(repo, "checkout", "-q", "-b", store.branch_for(it.row()))
    write(repo, "docs/p.md", "## Task 1 — the item's\n")
    commit_all(repo, "the plan")
    git(repo, "checkout", "-q", "main")
    write(repo, "docs/p.md", "## Task 1 — theirs\n## Task 2 — theirs\n## Task 3 — theirs\n")
    row = {**dict(it.row()), "attachments": json.dumps([{"kind": "plan", "path": "docs/p.md"}])}

    note = prompts.progress_note(WORKER, row, repo, run_dirs=run_dirs)

    assert "This plan has 1 tasks." in note


# -- review_threads_note (review threads anywhere §1) -----------------------------


async def _published(database, **kw):
    args = dict(wid="w1", gate=None, anchor_sha="h", body="evict LRU", label="must_fix")
    args.update(kw)
    tid = await database.write(lambda c: store.create_thread(c, **args))
    await database.write(
        lambda c: store.submit_review(
            c, wid="w1", gate=None, outcome="comment", summary=None, head_sha="h", base_sha="h"
        )
    )
    return tid


@REACHABLE
async def test_a_working_agent_is_told_to_address_and_reply(database, sandbox, reachable):
    """By `kraft item reply` where it can run it; by its summary where not."""
    await mk_item(database)
    tid = await _published(database)
    row = database.read(lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone())

    text = prompts.review_threads_note(WORKER, row, database, None, sandbox)

    assert f"[{tid}]" in text and "evict LRU" in text
    assert ("kraft item reply" in text) is reachable
    assert ("summary" in text) is not reachable


async def test_a_reviewer_judges_against_the_threads_and_does_not_reply(database):
    await mk_item(database)
    tid = await _published(database)
    row = database.read(lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone())

    text = prompts.review_threads_note(REVIEWER, row, database, None)

    assert f"[{tid}]" in text and "finding" in text
    assert "kraft item reply" not in text


async def test_answered_resolved_and_already_in_the_note_threads_are_left_out(database):
    await mk_item(database)
    answered = await _published(database, body="a")
    resolved = await _published(database, body="b")
    in_note = await _published(database, body="c")
    await database.write(
        lambda c: store.agent_reply(
            c, answered, author="implementation", body="done", claim="fixed", attempt=None
        )
    )
    await database.write(lambda c: store.set_thread_state(c, resolved, "resolved"))
    row = database.read(lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone())

    assert prompts.review_threads_note(WORKER, row, database, f"[{in_note}] a.py") == ""


@pytest.mark.parametrize(
    ("cause", "told", "not_told"),
    [
        ("code", "passed its own pipeline, but failed once combined with main", "never reached"),
        ("not_added", "never reached the merge train or queue: its own pipeline failed", "passed"),
        ("conflict", "no longer merges cleanly with main as it is now", "passed"),
    ],
    ids=["code", "not-added", "conflict"],
)
def test_the_merge_rejection_repair_is_told_what_happened_by_cause(cause, told, not_told):
    """A merge request that never reached the train never passed its own
    pipeline: the repair is not told that it did."""
    drop = {"cause": cause, "reason": "r", "rebased": True}

    note = prompts.merge_rejected_note("kraft/w1", "main", drop, in_place=False)

    assert note.startswith("A merge train or merge queue rejected this merge request. Branch")
    assert told in note
    assert not_told not in note
