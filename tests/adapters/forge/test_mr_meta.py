"""MRMeta: the merge request metadata an agent authored, read back for
publication. Everything an agent wrote is untrusted input about to become argv
and a public description, so the validation table is most of this file."""

from __future__ import annotations

from pathlib import Path

import pytest
from support.harness import write

from kraft import item_paths
from kraft.adapters.forge import mr as mr_ops
from kraft.adapters.forge.mr import MRMeta
from kraft.paths import RunDirs

WID = "a" * 32
#: Where an item that ran before the upgrade has it, in its worktree.
OLD = f".engineering/mr_metas/{WID}.md"


def _meta(base: Path, front: str, body: str, rel: str = OLD) -> Path:
    return write(base, rel, f"---\n{front}---\n{body}")


def _run(tmp_path: Path) -> RunDirs:
    return RunDirs(tmp_path / "run")


@pytest.mark.parametrize(
    "rel", [OLD, item_paths.document_name(WID, "mr_meta")], ids=["older-worktree", "docs"]
)
def test_read_mr_meta_reads_every_field(tmp_path, rel):
    root, under = item_paths.locate(_run(tmp_path), WID, rel, worktree=tmp_path)
    _meta(
        root,
        "work_item_ids: [x]\ntitle: Publish auto-escalate controls\n"
        "labels: [release::minor, area::ui]\nassignees: [omid]\nreviewers: [ada, grace]\n",
        "## What this introduces\n\nTwo forms and a CLI door.\n",
        under,
    )
    meta = mr_ops.read_mr_meta(tmp_path, WID, _run(tmp_path))
    assert meta.title == "Publish auto-escalate controls"
    assert meta.labels == ("release::minor", "area::ui")
    assert meta.assignees == ("omid",)
    assert meta.reviewers == ("ada", "grace")
    assert meta.description.startswith("## What this introduces")


def test_read_mr_meta_absent_is_empty_not_an_error(tmp_path):
    assert mr_ops.read_mr_meta(tmp_path, WID, _run(tmp_path)) == MRMeta()


def test_read_mr_meta_front_matter_only_has_no_description(tmp_path):
    _meta(tmp_path, "title: T\n", "\n   \n")
    assert mr_ops.read_mr_meta(tmp_path, WID, _run(tmp_path)).description == ""


def test_read_mr_meta_malformed_front_matter_keeps_the_body(tmp_path):
    # split_front_matter already degrades bad YAML to ({}, body): the
    # description survives, the structured fields do not.
    _meta(tmp_path, "title: [unclosed\n", "Body survives.\n")
    meta = mr_ops.read_mr_meta(tmp_path, WID, _run(tmp_path))
    assert meta.title is None and meta.labels == ()
    assert meta.description == "Body survives."


@pytest.mark.parametrize(
    "rel", [OLD, item_paths.document_name(WID, "mr_meta")], ids=["older-worktree", "docs"]
)
def test_read_mr_meta_refuses_a_symlink_out_of_the_worktree(tmp_path, rel):
    outside = tmp_path / "outside.md"
    outside.write_text("---\ntitle: stolen\n---\nsecrets\n")
    planted = item_paths.path_of(_run(tmp_path), WID, rel, worktree=tmp_path / "wt")
    planted.parent.mkdir(parents=True)
    planted.symlink_to(outside)
    assert mr_ops.read_mr_meta(tmp_path / "wt", WID, _run(tmp_path)) == MRMeta()


def test_read_mr_meta_drops_hostile_and_oversized_values(tmp_path):
    _meta(
        tmp_path,
        "title: |\n  real title\n  second line\n"
        "labels: ['--web-url=http://evil', ok, '', 'has\\nnewline', 'x', 'y', 'z',"
        " 'a', 'b', 'c', 'd', 'e']\n"
        "assignees: notalist\nreviewers: [{}, 7, 'grace']\n",
        "Body.\n",
    )
    meta = mr_ops.read_mr_meta(tmp_path, WID, _run(tmp_path))
    assert meta.title == "real title", "a multi-line title is clipped to its first line"
    assert "--web-url=http://evil" not in meta.labels, "an argument that reads as a flag is dropped"
    assert "" not in meta.labels and all("\n" not in x for x in meta.labels)
    assert len(meta.labels) <= 10, "lists are capped"
    assert meta.assignees == (), "a scalar where a list belongs is dropped whole"
    assert meta.reviewers == ("grace",), "non-string entries are dropped, the rest survive"


def test_read_mr_meta_drops_an_over_long_scalar(tmp_path):
    _meta(tmp_path, f"labels: ['{'x' * 201}', keep]\n", "Body.\n")
    assert mr_ops.read_mr_meta(tmp_path, WID, _run(tmp_path)).labels == ("keep",)


_SIGN = f"_Opened by [Kraft](https://kraft-sdlc.dev/) for work item `{WID}`._"
_FOOTER = f"\n\n---\n{_SIGN}"


@pytest.mark.parametrize(
    "meta, kw, expected",
    [
        (
            (MRMeta(description="## What\n\nA new door."),),
            {"title": "Add a door", "brief": "The hall needs one."},
            f"## What\n\nA new door.{_FOOTER}",
        ),
        # Spec §5: a missing artifact still opens the MR -- the one path
        # `open_mr` must never fail on.
        ((), {}, _SIGN),
        ((MRMeta(),), {}, _SIGN),
        (
            (),
            {"title": "Add a door", "brief": "  The hall needs one.\n"},
            f"The hall needs one.{_FOOTER}",
        ),
        (
            (),
            {"title": "Add a door\nand a window", "brief": ""},
            f"Add a door\nand a window{_FOOTER}",
        ),
    ],
    ids=["authored", "no-meta", "empty-meta", "brief", "clipped-title-in-full"],
)
def test_mr_body_is_the_description_as_written_or_the_brief(meta, kw, expected):
    """No commit list or branch line: the forge shows both, and each sync wrote
    them back over a person's edit. With no description, the brief says what
    the change is for; the title only repeats when the MR's clipped it. Every
    body is signed, with no rule above the footer when nothing else is there."""
    assert mr_ops.mr_body(WID, *meta, **kw) == expected


def test_mr_body_truncates_a_runaway_description_with_a_visible_marker():
    body = mr_ops.mr_body(WID, MRMeta(description="x" * 200_000))
    assert len(body) <= mr_ops.MR_BODY_MAX_CHARS
    assert body.endswith(f"*(truncated -- read the diff, this description did not fit)*{_FOOTER}")


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
