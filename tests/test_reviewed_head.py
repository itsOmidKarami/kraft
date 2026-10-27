"""`kraft.reviewed_head`: the pin the final review leaves, and the only thing
that moves it, Kraft's own clean rewrite of that exact head. The merge's
check against it is walked in `tests/adapters/forge/test_run_reviewed_head.py`."""

from __future__ import annotations

import pytest
from support.harness import commit_all, git, make_repo, write

from kraft import events, reviewed_head, store

A, B, C = "a" * 40, "b" * 40, "c" * 40


@pytest.mark.parametrize(
    ("pin", "before", "after", "pinned"),
    [
        ({"/wt": A}, {"/wt": A}, {"/wt": B}, {"/wt": B}),
        ({"/wt": A, "/m": C}, {"/wt": A, "/m": B}, {"/wt": B, "/m": A}, {"/wt": B, "/m": C}),
        ({"/wt": A, "/m": C}, {"/wt": A, "/m": C}, {"/wt": A, "/m": B}, {"/wt": A, "/m": B}),
        ({"/wt": A}, {"/wt": B}, {"/wt": C}, {"/wt": A}),
        ({"/wt": A}, {"/wt": A}, {"/wt": None}, {"/wt": A}),
        (None, {"/wt": A}, {"/wt": B}, None),
    ],
    ids=[
        "the-approved-head-rebased",
        "only-the-approved-checkout-moves",
        "a-member-rebased",
        "an-unreviewed-head-rebased",
        "unreadable-after",
        "nothing-pinned",
    ],
)
async def test_only_a_rewrite_of_the_approved_head_moves_the_pin(
    database, pin, before, after, pinned
):
    """A rebase of the head the final review approved is that head, rebased:
    the pin follows it, checkout by checkout. A head nobody approved, rebased,
    is still one nobody approved, and a tip that cannot be read is never
    pinned. With nothing pinned, nothing is."""
    await database.write(
        lambda c: store.create_work_item(
            c, id="w1", repo="/r", bead_id="B", title="t", chain_template="t", chain_definition="{}"
        )
    )
    if pin is not None:
        approved = {"gate": "final", "by": "human", "tips": pin}
        await database.write(lambda c: events.append(c, "w1", "gate_approved", approved))

    await reviewed_head.repin(database, "w1", before, after, cause="rebase")

    assert database.read(lambda c: reviewed_head.pinned(c, "w1")) == pinned
    repinned = database.read(lambda c: events.read_after(c, 0, "w1"))
    moved = [e for e in repinned if e["type"] == "reviewed_head_repinned"]
    assert len(moved) == (1 if pin is not None and pinned != pin else 0)


@pytest.mark.parametrize(
    ("tips", "named"),
    [
        ({"/wt": A}, A),
        ({"/wt": A, "/m": B}, "sha256:"),
        ({"/m": B, "/wt": A}, "sha256:"),
        ({"/wt": A, "/m": C}, "differs"),
        ({"/wt": A, "/m": None}, None),
        ({"/wt": None}, None),
    ],
    ids=[
        "one-checkout",
        "two-checkouts",
        "two-checkouts-built-in-the-other-order",
        "two-checkouts-member-moved",
        "a-tip-unreadable",
        "the-only-tip-unreadable",
    ],
)
def test_the_review_head_token_names_every_checkout(tips, named):
    """The token a final-review decision names: the bare sha for one checkout,
    a digest of all of them for a workspace (the same whatever order they were
    read in, another once any member moves), and nothing when a tip cannot be
    read."""
    got = reviewed_head.token(tips)

    if named == "differs":
        assert got.startswith("sha256:")
        assert got != reviewed_head.token({"/wt": A, "/m": B})
    elif named == "sha256:":
        assert got.startswith("sha256:")
        assert got == reviewed_head.token({"/wt": A, "/m": B})
    else:
        assert got == named


@pytest.mark.parametrize("planted", [False, True], ids=["clean-brings-it-home", "lfsconfig-stops"])
async def test_bring_home_never_checks_out_over_a_moved_lfsconfig(tmp_path, planted):
    """`bring_home` fast-forwards the worktree onto the commits the forge
    holds, and that checkout runs git-lfs's smudge, which fetches from the
    server `.lfsconfig` names. One the worker left in the worktree stops it
    for a person, rather than letting the host talk to that server."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-q", "-b", "main", str(origin))
    repo = make_repo(tmp_path)
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "checkout", "-q", "-b", "kraft/x")
    git(repo, "push", "-q", "origin", "kraft/x")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", "-b", "kraft/x", str(origin), str(other))
    write(other, "theirs.txt", "a person's commit\n")
    commit_all(other, "a person's commit")
    git(other, "push", "-q", "origin", "kraft/x")
    tip = git(repo, "rev-parse", "kraft/x")
    if planted:
        write(repo, ".lfsconfig", "[lfs]\n\turl = http://127.0.0.1:1/elsewhere\n")
        with pytest.raises(RuntimeError, match=r"changes \.lfsconfig"):
            await reviewed_head.bring_home(repo, "kraft/x", in_place=False)
        assert git(repo, "rev-parse", "kraft/x") == tip
    else:
        assert await reviewed_head.bring_home(repo, "kraft/x", in_place=False)
