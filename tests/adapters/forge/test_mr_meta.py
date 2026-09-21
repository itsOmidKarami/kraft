"""MRMeta: the merge request metadata an agent authored, read back for
publication. Everything an agent wrote is untrusted input about to become argv
and a public description, so the validation table is most of this file."""

from __future__ import annotations

from pathlib import Path

import pytest

from kraft.adapters.forge import mr as mr_ops
from kraft.adapters.forge.mr import MRMeta

WID = "a" * 32


def _write(worktree: Path, front: str, body: str) -> None:
    d = worktree / ".engineering" / "mr_metas"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{WID}.md").write_text(f"---\n{front}---\n{body}")


def test_read_mr_meta_reads_every_field(tmp_path):
    _write(
        tmp_path,
        "work_item_ids: [x]\ntitle: Publish auto-escalate controls\n"
        "labels: [release::minor, area::ui]\nassignees: [omid]\nreviewers: [ada, grace]\n",
        "## What this introduces\n\nTwo forms and a CLI door.\n",
    )
    meta = mr_ops.read_mr_meta(tmp_path, WID)
    assert meta.title == "Publish auto-escalate controls"
    assert meta.labels == ("release::minor", "area::ui")
    assert meta.assignees == ("omid",)
    assert meta.reviewers == ("ada", "grace")
    assert meta.description.startswith("## What this introduces")


def test_read_mr_meta_absent_is_empty_not_an_error(tmp_path):
    assert mr_ops.read_mr_meta(tmp_path, WID) == MRMeta()


def test_read_mr_meta_front_matter_only_has_no_description(tmp_path):
    _write(tmp_path, "title: T\n", "\n   \n")
    assert mr_ops.read_mr_meta(tmp_path, WID).description == ""


def test_read_mr_meta_malformed_front_matter_keeps_the_body(tmp_path):
    # split_front_matter already degrades bad YAML to ({}, body): the
    # description survives, the structured fields do not.
    _write(tmp_path, "title: [unclosed\n", "Body survives.\n")
    meta = mr_ops.read_mr_meta(tmp_path, WID)
    assert meta.title is None and meta.labels == ()
    assert meta.description == "Body survives."


def test_read_mr_meta_refuses_a_symlink_out_of_the_worktree(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("---\ntitle: stolen\n---\nsecrets\n")
    d = tmp_path / "wt" / ".engineering" / "mr_metas"
    d.mkdir(parents=True)
    (d / f"{WID}.md").symlink_to(outside)
    assert mr_ops.read_mr_meta(tmp_path / "wt", WID) == MRMeta()


def test_read_mr_meta_drops_hostile_and_oversized_values(tmp_path):
    _write(
        tmp_path,
        "title: |\n  real title\n  second line\n"
        "labels: ['--web-url=http://evil', ok, '', 'has\\nnewline', 'x', 'y', 'z',"
        " 'a', 'b', 'c', 'd', 'e']\n"
        "assignees: notalist\nreviewers: [{}, 7, 'grace']\n",
        "Body.\n",
    )
    meta = mr_ops.read_mr_meta(tmp_path, WID)
    assert meta.title == "real title", "a multi-line title is clipped to its first line"
    assert "--web-url=http://evil" not in meta.labels, "an argument that reads as a flag is dropped"
    assert "" not in meta.labels and all("\n" not in x for x in meta.labels)
    assert len(meta.labels) <= 10, "lists are capped"
    assert meta.assignees == (), "a scalar where a list belongs is dropped whole"
    assert meta.reviewers == ("grace",), "non-string entries are dropped, the rest survive"


def test_read_mr_meta_drops_an_over_long_scalar(tmp_path):
    _write(tmp_path, f"labels: ['{'x' * 201}', keep]\n", "Body.\n")
    assert mr_ops.read_mr_meta(tmp_path, WID).labels == ("keep",)


def test_mr_body_leads_with_the_description_and_demotes_the_commits():
    meta = MRMeta(description="## What\n\nA new door.")
    body = mr_ops.mr_body(WID, "kraft/abc", ("first commit",), meta)
    assert body.startswith("## What\n\nA new door.")
    assert body.index("A new door.") < body.index("<details>") < body.index("- first commit")
    assert "<summary>Commits on this branch</summary>" in body
    assert "Branch `kraft/abc`" in body
    assert body.rstrip().endswith("Review the diff and the pipeline before merging.")
    assert "Opened by Kraft for work item" not in body


_TAIL = "Branch `kraft/abc`. Review the diff and the pipeline before merging."


@pytest.mark.parametrize(
    "commits, expected",
    [
        (
            ("first commit",),
            f"Opened by Kraft for work item {WID}.\n\n"
            f"Commits on this branch:\n\n- first commit\n\n{_TAIL}",
        ),
        (
            ("spec: batch", "plan: batch", "the work"),
            f"Opened by Kraft for work item {WID}.\n\n"
            f"Commits on this branch:\n\n- spec: batch\n- plan: batch\n- the work\n\n{_TAIL}",
        ),
        # `git log` returning nothing (`commits_on` swallows a git failure)
        # must not leave a dangling "Commits:" header.
        ((), f"Opened by Kraft for work item {WID}.\n\n{_TAIL}"),
    ],
    ids=["one-commit", "oldest-first", "no-commits-no-header"],
)
@pytest.mark.parametrize("meta", [(), (MRMeta(),)], ids=["no-meta", "empty-meta"])
def test_mr_body_without_meta_is_the_old_default_body(commits, expected, meta):
    """Spec §5: a missing artifact produces today's body, byte for byte -- the
    one path `open_mr` must never fail on."""
    assert mr_ops.mr_body(WID, "kraft/abc", commits, *meta) == expected


def test_mr_body_truncates_a_runaway_description_with_a_visible_marker():
    body = mr_ops.mr_body(WID, "kraft/abc", (), MRMeta(description="x" * 200_000))
    assert len(body) <= mr_ops.MR_BODY_MAX_CHARS
    assert body.endswith("*(truncated -- read the diff, this description did not fit)*")


@pytest.mark.parametrize(
    "title, expected",
    [
        (
            "CLI/UX cleanup batch — Kraft-97e, sws6, 5lpl: worktree-aware probe_repo, "
            "`kraft disconnect` and `kraft retry` verbs, and a good deal more besides.",
            "CLI/UX cleanup batch — Kraft-97e, sws6, 5lpl: worktree-aware probe_repo…",
        ),
        ("  Teach probe_repo about worktrees\nand more  ", "Teach probe_repo about worktrees"),
        # glab and gh both refuse an empty --title; a stuck node is worse
        # than a dull one.
        ("   ", "Kraft work item"),
    ],
    ids=["long-clipped-to-a-headline", "first-line-stripped", "empty-names-something"],
)
def test_mr_title_is_one_headline(title, expected):
    """A work item title is a paragraph; a forge title is a headline."""
    out = mr_ops.mr_title(title)
    assert out == expected
    assert len(out) <= mr_ops.MR_TITLE_MAX


def test_mr_title_for_prefers_the_authored_title():
    assert mr_ops.mr_title_for("work item title", MRMeta(title="Authored")) == "Authored"
    assert mr_ops.mr_title_for("work item title", MRMeta()) == "work item title"
    long = "L" * 200
    assert len(mr_ops.mr_title_for("x", MRMeta(title=long))) == mr_ops.MR_TITLE_MAX
