"""The calls Kraft makes to reply in a bot's review thread, against a real GitHub and
a real GitLab. They write to a scratch pull request and merge request that the person
running them names, so they skip without those variables: a Kraft worker has none, and
must never point them at a real item's merge request. The controller runs them before
`final_review` and pastes the recorded properties into the merge request (the spec's
"Acceptance gate")."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from support.forge_contract import cli_json, gh_checkout

from kraft.adapters import forge
from kraft.adapters.forge.gh import _THREADS_QUERY
from kraft.adapters.forge.replies.facts import body_hash, reply_marker
from kraft.adapters.forge.replies.send import classify

_UNRESOLVE = """
mutation($thread: ID!) {
  unresolveReviewThread(input: {threadId: $thread}) { thread { isResolved } }
}
"""
_MARK = reply_marker("e2etest0-0123456789ab")


async def _undo(steps: list) -> None:
    """Run every registered cleanup, newest first; one that fails does not stop the rest."""
    for step in reversed(steps):
        try:
            await step()
        except Exception:  # noqa: BLE001 -- a scratch write's cleanup is best effort
            pass


@pytest.mark.e2e("gh")
async def test_gh_thread_writes_against_a_real_pull_request(tmp_path, record_property):
    """The calls `GhCli` makes for a bot thread, on a real pull request: the viewer,
    one thread read, a reply (its REST id and node id both match the note GraphQL
    reads back), a resolve (undone), a delete, a top-level comment created, edited
    and deleted, and a reply refused while a pending review is open, which `classify`
    reads as deferred. Needs `KRAFT_E2E_GH_REPO` (owner/name) and
    `KRAFT_E2E_GH_REPLY_PR`, a pull request with at least one open review thread you
    may write in. Every write is undone."""
    slug, number = os.environ.get("KRAFT_E2E_GH_REPO"), os.environ.get("KRAFT_E2E_GH_REPLY_PR")
    if not (slug and number):
        pytest.skip("e2e: set KRAFT_E2E_GH_REPO (owner/name) and KRAFT_E2E_GH_REPLY_PR")
    owner, _, name = slug.partition("/")
    gh, repo = forge.GhCli(), gh_checkout(tmp_path, slug)
    branch = cli_json(["gh", "pr", "view", number, "--repo", slug, "--json", "headRefName"])
    branch = branch["headRefName"]

    assert await gh.viewer(repo=repo) == cli_json(["gh", "api", "user"])["login"]
    data = cli_json(
        ["gh", "api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"n={number}",
         "-f", f"query={_THREADS_QUERY}"]
    )  # fmt: skip
    open_threads = [
        t
        for t in data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        if not t["isResolved"]
    ]
    if not open_threads:
        pytest.skip(f"e2e: {slug}#{number} has no open review thread to reply in")
    thread_id = open_threads[0]["id"]
    thread = await gh.read_thread(repo=repo, branch=branch, thread_id=thread_id)
    assert thread is not None and thread.mr == int(number) and thread.notes

    undo: list = []
    try:
        posted = await gh.reply_to_thread(
            repo=repo, thread=thread, body=f"Kraft contract test.\n\n{_MARK}"
        )
        record_property("gh_reply_ids", posted.ids)
        assert len(posted.ids) == 2 and posted.ids[0].isdigit()
        mine = forge.ForgeNote(posted.ids[1], posted.ids[0], "", None, posted.body, "", "")
        undo.append(lambda: gh.delete_note(repo=repo, branch=branch, note=mine, in_thread=True))
        after = await gh.read_thread(repo=repo, branch=branch, thread_id=thread_id)
        found = [n for n in after.notes if n.rest_id == posted.ids[0] and n.id == posted.ids[1]]
        assert len(found) == 1, "the REST id and the node id name one note"
        assert body_hash(found[0].body) == body_hash(posted.body)

        undo.append(lambda: _gh_unresolve(thread_id))
        await gh.resolve_thread(repo=repo, thread=after)
        assert (await gh.read_thread(repo=repo, branch=branch, thread_id=thread_id)).resolved
        await _gh_unresolve(thread_id)

        await gh.delete_note(repo=repo, branch=branch, note=found[0], in_thread=True)
        gone = await gh.read_thread(repo=repo, branch=branch, thread_id=thread_id)
        assert posted.ids[0] not in [n.rest_id for n in gone.notes]

        made = await gh.upsert_comment(
            repo=repo, branch=branch, note_id=None, body="Kraft contract test"
        )
        comment = forge.ForgeNote(made.ids[1], made.ids[0], "", None, "", "", "")
        undo.append(lambda: gh.delete_note(repo=repo, branch=branch, note=comment, in_thread=False))
        await gh.upsert_comment(
            repo=repo, branch=branch, note_id=made.ids[0], body="Kraft contract test, edited"
        )
        listed = await gh.mr_comments(repo=repo, branch=branch)
        assert [c.body for c in listed if c.rest_id == made.ids[0]] == [
            "Kraft contract test, edited"
        ]

        review = cli_json(
            [
                "gh",
                "api",
                f"repos/{slug}/pulls/{number}/reviews",
                "-X",
                "POST",
                "-f",
                "body=kraft contract test",
            ]
        )
        undo.append(lambda: _gh_delete_review(slug, number, review["id"]))
        with pytest.raises(forge.ForgeError) as refused:
            await gh.reply_to_thread(
                repo=repo, thread=thread, body=f"Kraft contract test.\n\n{_MARK}"
            )
        record_property("gh_pending_review_error", str(refused.value))
        assert classify(refused.value) == "deferred"
    finally:
        await _undo(undo)


async def _gh_unresolve(thread_id: str) -> None:
    cli_json(["gh", "api", "graphql", "-f", f"query={_UNRESOLVE}", "-f", f"thread={thread_id}"])


async def _gh_delete_review(slug: str, number: str, review_id: int) -> None:
    cli_json(["gh", "api", "-X", "DELETE", f"repos/{slug}/pulls/{number}/reviews/{review_id}"])


@pytest.mark.e2e("glab")
async def test_glab_thread_writes_against_a_real_merge_request(record_property):
    """The calls `GlabCli` makes for a discussion, on a real merge request: the viewer,
    one discussion read, a reply, a resolve (undone), a delete, and a top-level comment
    created, edited, listed and deleted. Needs a checkout of a GitLab project
    (`KRAFT_E2E_GLAB_REPO`) and the iid of one of its merge requests with an open
    discussion (`KRAFT_E2E_GLAB_REPLY_MR`). Every write is undone."""
    where, iid = os.environ.get("KRAFT_E2E_GLAB_REPO"), os.environ.get("KRAFT_E2E_GLAB_REPLY_MR")
    if not (where and iid):
        pytest.skip("e2e: set KRAFT_E2E_GLAB_REPO and KRAFT_E2E_GLAB_REPLY_MR")
    glab, repo = forge.GlabCli(), Path(where)
    cwd = repo
    mr = cli_json(["glab", "api", f"projects/:id/merge_requests/{iid}"], cwd=repo)
    branch = mr["source_branch"]

    assert await glab.viewer(repo=repo) == cli_json(["glab", "api", "user"], cwd=repo)["username"]
    discussions = cli_json(
        ["glab", "api", f"projects/:id/merge_requests/{iid}/discussions?per_page=100"], cwd=repo
    )
    open_ones = [
        d
        for d in discussions
        if any(n.get("resolvable") for n in d["notes"])
        and not all(n.get("resolved") for n in d["notes"] if n.get("resolvable"))
    ]
    if not open_ones:
        pytest.skip(f"e2e: !{iid} has no open discussion to reply in")
    thread_id = open_ones[0]["id"]
    thread = await glab.read_thread(repo=repo, branch=branch, thread_id=thread_id)
    assert thread is not None and thread.mr == int(iid) and thread.notes

    undo: list = []
    try:
        posted = await glab.reply_to_thread(
            repo=repo, thread=thread, body=f"Kraft contract test.\n\n{_MARK}"
        )
        record_property("glab_reply_ids", posted.ids)
        assert len(posted.ids) == 1
        mine = forge.ForgeNote(posted.ids[0], posted.ids[0], "", None, posted.body, "", "")
        undo.append(lambda: glab.delete_note(repo=repo, branch=branch, note=mine, in_thread=True))
        after = await glab.read_thread(repo=repo, branch=branch, thread_id=thread_id)
        found = [n for n in after.notes if n.id == posted.ids[0]]
        assert len(found) == 1 and body_hash(found[0].body) == body_hash(posted.body)

        undo.append(lambda: _glab_unresolve(cwd, iid, thread_id))
        await glab.resolve_thread(repo=repo, thread=after)
        assert (await glab.read_thread(repo=repo, branch=branch, thread_id=thread_id)).resolved
        await _glab_unresolve(cwd, iid, thread_id)

        await glab.delete_note(repo=repo, branch=branch, note=found[0], in_thread=True)
        gone = await glab.read_thread(repo=repo, branch=branch, thread_id=thread_id)
        assert posted.ids[0] not in [n.id for n in gone.notes]

        made = await glab.upsert_comment(
            repo=repo, branch=branch, note_id=None, body="Kraft contract test"
        )
        comment = forge.ForgeNote(made.ids[0], made.ids[0], "", None, "", "", "")
        undo.append(
            lambda: glab.delete_note(repo=repo, branch=branch, note=comment, in_thread=False)
        )
        await glab.upsert_comment(
            repo=repo, branch=branch, note_id=made.ids[0], body="Kraft contract test, edited"
        )
        listed = await glab.mr_comments(repo=repo, branch=branch)
        assert [c.body for c in listed if c.id == made.ids[0]] == ["Kraft contract test, edited"]
    finally:
        await _undo(undo)


async def _glab_unresolve(cwd: Path, iid: str, thread_id: str) -> None:
    cli_json(
        [
            "glab",
            "api",
            "-X",
            "PUT",
            f"projects/:id/merge_requests/{iid}/discussions/{thread_id}",
            "-F",
            "resolved=false",
        ],
        cwd=cwd,
    )
