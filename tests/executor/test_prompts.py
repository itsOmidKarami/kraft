"""`executor.prompts`: the notes an agent instruction is assembled from.

That `dispatch_node` puts them in the prompt, in order, is test_dispatch's."""

import pytest
from support.harness import _git, v1_named_chain

from kraft.config import git_read
from kraft.executor import prompts
from kraft.findings import Finding, JobRef
from kraft.templates.models import AgentTask


def _task(skill: str | None = None) -> AgentTask:
    """An agent task; `skill` set means it states its own method."""
    return AgentTask.model_validate(
        {"id": "t", "kind": "agent", "harness": "claude", "prompt": "p", "skill": skill}
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


def _plan_on_disk(worktree):
    (worktree / ".engineering" / "plans").mkdir(parents=True)
    (worktree / ".engineering" / "plans" / "w1.md").write_text("# the plan\n")


def test_a_steered_node_with_no_artifact_yet_keeps_the_plain_steer_prompt(tmp_path):
    """The branch fires on the document's existence, not on any gate name: a
    node whose artifact was never written has nothing to revise."""
    with_artifact = prompts.steer_prefix("plan", {"id": "w1"}, tmp_path, "go left")
    without = prompts.steer_prefix(None, {"id": "w1"}, tmp_path, "go left")

    assert with_artifact == without == "A human has steered this run: go left\n\n"


@pytest.mark.parametrize(
    ("source", "present", "absent"),
    [
        # Kraft-bol: a rejected plan cost a full re-plan because the dispatch
        # never mentioned the document the agent had already written.
        ("human", [".engineering/plans/w1.md", "evise", "A human read"], ["A human has steered"]),
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
def test_steer_prefix_names_the_notes_author(tmp_path, source, present, absent):
    """Both human templates name an author: `_STEER_PROMPT` says "A human has
    steered this run", and over an existing artifact `_REVISE_PROMPT` says "A
    human read ... and sent it back with this note". A note Kraft or an agent
    wrote gets neither."""
    _plan_on_disk(tmp_path)
    note = "Findings the last review of this node left unresolved:\n- [important] a.py:1 — x"

    # A human is the default: every caller that passes no source still means one.
    kwargs = {} if source == "human" else {"source": source}
    out = prompts.steer_prefix("plan", {"id": "w1"}, tmp_path, note, **kwargs)

    assert note in out
    assert all(text in out for text in present), out
    assert not any(text in out for text in absent), out


# -- scope_note -------------------------------------------------------------------


_SCOPES = {
    "test_scopes": [
        {"paths": ["frontend/**"], "command": "just test-ui"},
        {"paths": ["frontend/**"], "command": "just e2e-ci"},
        {"paths": ["src/**", "tests/**"], "command": "just ci-test"},
    ]
}


def test_scope_note_lists_every_scope_command_for_the_implementer():
    out = prompts.scope_note(_task(), _SCOPES)
    for cmd in ("just test-ui", "just e2e-ci", "just ci-test"):
        assert cmd in out
    assert "frontend/**" in out


def test_scope_note_is_empty_for_any_other_hook():
    """V1 keys this on the task stating a method of its own (a `skill:`), not
    on a hook name: every task with a skill is "any other hook" now."""
    for skill in ("kraft:mr-description", "kraft:code-review", "kraft:spec"):
        assert prompts.scope_note(_task(skill), _SCOPES) == ""


def test_scope_note_is_empty_with_no_scopes_configured():
    assert prompts.scope_note(_task(), {}) == ""
    assert prompts.scope_note(_task(), None) == ""


def test_scope_note_handles_a_legacy_bare_test_command():
    # config.load_repos wraps a bare test_command into a ["**"] scope, but a
    # LaunchContext built by hand may not have been through that.
    out = prompts.scope_note(_task(), {"test_command": "just test"})
    assert "just test" in out


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
    old_base = git_read(repo, "rev-parse", "HEAD")
    for name, text in files.items():
        (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return old_base, git_read(repo, "rev-parse", "HEAD")


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
