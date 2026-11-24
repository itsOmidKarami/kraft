"""A re-describe of a merge request: what Kraft wrote is replaced, what a person
wrote is not."""

from __future__ import annotations

import pytest

from kraft import events, store
from kraft.adapters import forge
from kraft.adapters.forge.github_api import GitHubApi
from kraft.executor import prompts


def _authored(worktree, text: str, front: str = "") -> str:
    """What a `describe_merge_request` leaves; returns the description."""
    meta = worktree / ".engineering" / "mr_metas" / "w1.md"
    meta.parent.mkdir(parents=True, exist_ok=True)
    body = f"## What this introduces\n\n{text}"
    meta.write_text(f"---\nwork_item_ids: [w1]\n{front}---\n{body}\n")
    return body


def _brief_note(run_forge, worktree) -> str:
    return prompts.mr_description_note(
        run_forge.database, "w1", run_dirs=run_forge.run_dirs, worktree=worktree
    )


@pytest.mark.parametrize("handler", ["open_mr", "sync_mr"])
@pytest.mark.parametrize(
    "edit, kept",
    [
        (lambda body: body, False),
        (lambda body: body.replace("\n", "\r\n") + "\n", False),
        (lambda body: body + "\n\n## Changelog\n- A person's line.", True),
        (lambda body: body.replace("A door", "A person's door"), True),
        (lambda body: "", True),
    ],
    ids=["untouched", "forge-round-trip", "changelog-edit", "other-section-edit", "emptied"],
)
async def test_a_re_describe_replaces_only_a_description_kraft_wrote(
    run_forge, tmp_path, handler, edit, kept
):
    """Whole or not at all, so nothing can come out half-merged, and a label a
    person changed survives either way: no path here writes one."""
    _authored(tmp_path, "A door.", "labels: [release::none]\n")
    fake = forge.FakeForge()
    await run_forge(fake, "open_mr", "x1")
    live = edit(fake.opened_bodies[1])
    fake.bodies["kraft/w1"] = live
    fake.labeled.append("release::patch")
    new = _authored(tmp_path, "A window.")

    await run_forge(fake, handler, "x2")

    assert fake.labeled == ["release::patch"]
    if kept:
        assert fake.bodies["kraft/w1"] == live
        assert [e["repo"] for e in run_forge.events("mr_description_kept")] == [str(tmp_path)]
        assert "not the one Kraft last wrote" in run_forge.log("x2")
    else:
        assert fake.bodies["kraft/w1"].startswith(new)
        assert run_forge.events("mr_description_kept") == []


@pytest.mark.parametrize("handler", ["open_mr", "sync_mr"])
@pytest.mark.parametrize(
    "live, opened_number, regenerated",
    [
        ("Written before Kraft kept a record.", None, False),
        ("", None, False),
        ("Another merge request's text.", 99, False),
        ("The body an earlier Kraft wrote.", 1, True),
    ],
    ids=["not-kraft-text", "not-kraft-empty", "kraft-opened-another", "kraft-opened-this"],
)
async def test_a_merge_request_with_no_record_is_kept_unless_kraft_opened_it(
    run_forge, tmp_path, handler, live, opened_number, regenerated
):
    fake = forge.FakeForge()
    await fake.open_mr(repo=tmp_path, branch="kraft/w1", base="main", title="t", body=live)
    await run_forge(fake, "mark_ready", "x1")  # files the item the event below belongs to
    if opened_number is not None:
        payload = {"number": opened_number, "url": "u"}
        await run_forge.database.write(
            lambda c: events.append(c, "w1", forge.ForgeEvent.MR_OPENED, payload)
        )

    await run_forge(fake, handler, "x2")
    # A reuse records `mr_opened` itself, which must not vouch for it next time.
    await run_forge(fake, handler, "x3")

    body = await fake.mr_body(repo=tmp_path, branch="kraft/w1")
    assert (body != live) is regenerated
    assert bool(run_forge.events("mr_description_kept")) is not regenerated


@pytest.mark.parametrize(
    "row_number, regenerated",
    [(None, False), (0, False), (1, True), (2, False)],
    ids=[
        "single-repo",
        "workspace-no-proof",
        "workspace-row-vouches",
        "workspace-row-names-another",
    ],
)
async def test_a_failed_first_read_does_not_turn_a_reused_merge_request_into_kraft_s(
    run_forge, tmp_path, row_number, regenerated
):
    """Nor does it take away a workspace row's proof that an earlier Kraft wrote it."""

    class _FailsOnce(forge.FakeForge):
        failed = False

        async def mr_body(self, *, repo, branch):
            if not self.failed:
                self.failed = True
                raise forge.ForgeError("cannot read the description")
            return await super().mr_body(repo=repo, branch=branch)

    live = "Opened by hand."
    fake = _FailsOnce()
    await fake.open_mr(repo=tmp_path, branch="kraft/w1", base="main", title="t", body=live)
    if row_number is not None:
        await run_forge(fake, "mark_ready", "x0")  # files the item the row belongs to

        def _seed(c) -> None:
            # A member, with a root that has no source change of its own.
            row = store.add_repo(
                c, work_item_id="w1", repo_path=str(tmp_path), role="submodule", merge_rank=0
            )
            root = str(tmp_path / "root")
            store.add_repo(c, work_item_id="w1", repo_path=root, role="root", merge_rank=1)
            if row_number:
                ref = {"number": row_number, "url": "u"}
                store.update_repo_state(c, row, merge_state="open", mr_ref=ref)

        await run_forge.database.write(_seed)

    await run_forge(fake, "open_mr", "x1")
    await run_forge(fake, "sync_mr", "x2")

    assert (await fake.mr_body(repo=tmp_path, branch="kraft/w1") != live) is regenerated


@pytest.mark.parametrize(
    "row_number, event_number, unread, wrote",
    [
        (1, None, False, True),
        (2, None, False, False),
        (None, 1, False, False),
        (1, None, True, False),
    ],
    ids=[
        "its-row-names-it",
        "its-row-names-another",
        "only-a-sibling-event-names-it",
        "its-row-came-from-a-failed-read",
    ],
)
async def test_a_workspace_member_asks_its_own_row_who_opened_it(
    run_forge, tmp_path, row_number, event_number, unread, wrote
):
    fake = forge.FakeForge()
    await fake.open_mr(repo=tmp_path, branch="kraft/w1", base="main", title="t", body="b")
    await run_forge(fake, "mark_ready", "x1")

    def _seed(c) -> int:
        row = store.add_repo(
            c, work_item_id="w1", repo_path=str(tmp_path), role="submodule", merge_rank=0
        )
        if row_number is not None:
            ref = {
                "number": row_number,
                "url": "u",
                **({"description_unread": 1} if unread else {}),
            }
            store.update_repo_state(c, row, merge_state="open", mr_ref=ref)
        if event_number is not None:
            payload = {"number": event_number, "url": "u"}
            events.append(c, "w1", forge.ForgeEvent.MR_OPENED, payload)
        return row

    row = await run_forge.database.write(_seed)

    assert (
        run_forge.database.read(
            lambda c: forge.run._wrote_before_records(c, "w1", tmp_path, row, 1)
        )
        is wrote
    )


async def test_a_dropped_connection_does_not_mark_kraft_s_description_kept(run_forge, tmp_path):
    """An older item whose read fails once is regenerated on the next pass."""

    class _DropsOnce(forge.FakeForge):
        dropped = False

        async def mr_body(self, *, repo, branch):
            if not self.dropped:
                self.dropped = True
                raise forge.ForgeError("connection reset", no_answer=True)
            return await super().mr_body(repo=repo, branch=branch)

    fake = _DropsOnce()
    await fake.open_mr(repo=tmp_path, branch="kraft/w1", base="main", title="t", body="old")
    await run_forge(fake, "mark_ready", "x1")
    payload = {"number": 1, "url": "u"}
    await run_forge.database.write(
        lambda c: events.append(c, "w1", forge.ForgeEvent.MR_OPENED, payload)
    )
    new = _authored(tmp_path, "A window.")

    await run_forge(fake, "sync_mr", "x2")
    assert run_forge.events("mr_description_kept") == []
    assert "kraft/w1" not in fake.bodies

    await run_forge(fake, "sync_mr", "x3")

    assert fake.bodies["kraft/w1"].startswith(new)
    assert run_forge.events("mr_description_kept") == []


async def test_a_failed_first_write_on_an_older_item_does_not_freeze_it(run_forge, tmp_path):
    """The text an earlier Kraft wrote stays Kraft's after a write that never landed."""

    class _FailsOnce(forge.FakeForge):
        failed = False

        async def update_mr(self, *, repo, branch, body):
            if not self.failed:
                self.failed = True
                raise forge.ForgeError("the write did not land")
            await super().update_mr(repo=repo, branch=branch, body=body)

    fake = _FailsOnce()
    await fake.open_mr(repo=tmp_path, branch="kraft/w1", base="main", title="t", body="old")
    await run_forge(fake, "mark_ready", "x1")
    payload = {"number": 1, "url": "u"}
    await run_forge.database.write(
        lambda c: events.append(c, "w1", forge.ForgeEvent.MR_OPENED, payload)
    )
    new = _authored(tmp_path, "A window.")

    await run_forge(fake, "sync_mr", "x2")
    await run_forge(fake, "sync_mr", "x3")

    assert fake.bodies["kraft/w1"].startswith(new)
    assert run_forge.events("mr_description_kept") == []


async def test_a_seeded_open_whose_body_never_landed_is_rewritten(run_forge, tmp_path):
    """GitLab creates with `open_seed` and then writes the body: a retry after the
    second write failed finds the seed, which is Kraft's, not a person's."""

    class _BodyLost(forge.FakeForge):
        async def open_mr(self, **kw):
            await super().open_mr(**{**kw, "body": self.open_seed})
            raise forge.ForgeError("connection reset before the body was written")

    new = _authored(tmp_path, "A door.")
    fake = _BodyLost(open_seed="Kraft is writing this.")
    await run_forge(fake, "open_mr", "x1")

    await run_forge(fake, "open_mr", "x2")

    assert fake.bodies["kraft/w1"].startswith(new)
    assert run_forge.events("mr_description_kept") == []


@pytest.mark.parametrize("seed", [None, "Kraft is writing this."], ids=["github", "gitlab"])
async def test_a_root_pointer_bump_records_the_description_it_opens_with(
    run_forge, tmp_path, monkeypatch, seed
):
    async def refused(*_, **__):
        raise forge.ForgeError("protected branch")

    async def moved(*_):
        return ["member"]

    fake = forge.FakeForge(open_seed=seed)
    await fake.open_mr(repo=tmp_path, branch="other", base="main", title="t", body="b")
    await run_forge(fake, "mark_ready", "x1")
    row = await run_forge.database.write(
        lambda c: store.add_repo(
            c, work_item_id="w1", repo_path=str(tmp_path), role="root", merge_rank=0
        )
    )
    monkeypatch.setattr(forge.git, "run_git", refused)
    monkeypatch.setattr(forge.run, "_point_at_merged_members", moved)

    await forge.run._bump_pointer_only_root(
        fake,
        run_forge.database,
        [{"id": row, "role": "root", "repo_path": str(tmp_path)}],
        branch="kraft/w1",
        base="main",
        title="t",
        work_item_id="w1",
        node_id="merge",
        orig_repo=tmp_path,
    )

    assert [e["sha256"] for e in run_forge.events("mr_description_written")] == [
        forge.mr.body_hash(t) for t in (seed, fake.opened_bodies[2]) if t is not None
    ]


async def test_a_write_after_a_kept_description_clears_the_brief_note(run_forge, tmp_path):
    """Someone reverts to Kraft's own text: the next sync writes, so the brief must
    no longer say the description was kept."""
    _authored(tmp_path, "A door.")
    fake = forge.FakeForge()
    await run_forge(fake, "open_mr", "x1")
    ours = fake.opened_bodies[1]
    fake.bodies["kraft/w1"] = "A person's description."
    await run_forge(fake, "sync_mr", "x2")
    assert _brief_note(run_forge, tmp_path)

    fake.bodies["kraft/w1"] = ours
    await run_forge(fake, "sync_mr", "x3")

    assert _brief_note(run_forge, tmp_path) == ""


async def test_a_description_write_whose_answer_was_lost_is_not_read_as_an_edit(
    run_forge, tmp_path
):
    class _LostAnswer(forge.FakeForge):
        async def update_mr(self, *, repo, branch, body):
            first = not self.bodies
            await super().update_mr(repo=repo, branch=branch, body=body)
            if first:
                raise forge.ForgeError("connection reset after the write")

    _authored(tmp_path, "A door.")
    fake = _LostAnswer()
    await run_forge(fake, "open_mr", "x1")
    _authored(tmp_path, "A window.")
    await run_forge(fake, "sync_mr", "x2")
    third = _authored(tmp_path, "A third body.")

    await run_forge(fake, "sync_mr", "x3")

    assert fake.bodies["kraft/w1"].startswith(third)
    assert run_forge.events("mr_description_kept") == []


BOT, PERSON = True, False


@pytest.mark.parametrize("handler", ["open_mr", "sync_mr"])
@pytest.mark.parametrize(
    "saves, reaches_ours, regenerated",
    [
        ([BOT], True, True),
        ([BOT, BOT], True, True),
        ([BOT, PERSON], True, False),
        ([PERSON, BOT], True, False),
        ([BOT], False, False),
    ],
    ids=["bot", "two-bots", "bot-then-person", "person-then-bot", "history-stops-short"],
)
async def test_a_description_only_bots_edited_is_regenerated(
    run_forge, tmp_path, handler, saves, reaches_ours, regenerated
):
    """`saves` is oldest first, each on top of Kraft's own text."""
    _authored(tmp_path, "A door.")
    fake = forge.FakeForge()
    await run_forge(fake, "open_mr", "x1")
    ours = fake.opened_bodies[1]
    texts = [f"{ours}\n\nsave {i}" for i in range(len(saves))]
    history = [forge.DescriptionRevision(t, b) for t, b in zip(texts, saves, strict=True)]
    if reaches_ours:
        history.insert(0, forge.DescriptionRevision(ours, False))
    fake.description_history = tuple(reversed(history))
    fake.bodies["kraft/w1"] = texts[-1]
    new = _authored(tmp_path, "A window.")

    await run_forge(fake, handler, "x2")

    assert fake.bodies["kraft/w1"].startswith(new) is regenerated
    assert (run_forge.events("mr_description_kept") == []) is regenerated


@pytest.mark.parametrize(
    "history",
    [
        lambda ours: (),
        lambda ours: forge.ForgeError("history unavailable"),
        lambda ours: (
            forge.DescriptionRevision("a bot's older save", True),
            forge.DescriptionRevision(ours, False),
        ),
    ],
    ids=["no-history", "failed-read", "history-is-not-the-live-text"],
)
async def test_a_description_with_unknown_history_is_kept(run_forge, tmp_path, history):
    _authored(tmp_path, "A door.")
    fake = forge.FakeForge()
    await run_forge(fake, "open_mr", "x1")
    live = fake.opened_bodies[1] + "\n\nedited"
    fake.bodies["kraft/w1"] = live
    fake.description_history = history(fake.opened_bodies[1])
    _authored(tmp_path, "A window.")

    assert await run_forge(fake, "sync_mr", "x2") == ("done", "done")

    assert fake.bodies["kraft/w1"] == live
    assert len(run_forge.events("mr_description_kept")) == 1


async def test_an_untouched_description_reads_no_history(run_forge, tmp_path):
    _authored(tmp_path, "A door.")
    fake = forge.FakeForge()
    await run_forge(fake, "open_mr", "x1")
    _authored(tmp_path, "A window.")

    await run_forge(fake, "sync_mr", "x2")

    assert fake.description_history_reads == 0


@pytest.mark.parametrize(
    "client",
    [lambda: forge.GhCli(), lambda: GitHubApi("corp", None)],
    ids=["cli", "api"],
)
@pytest.mark.parametrize(
    "editor, bot",
    [({"__typename": "Bot"}, True), ({"__typename": "User"}, False), (None, False)],
    ids=["bot", "user", "ghost"],
)
async def test_github_reads_who_saved_each_description(monkeypatch, tmp_path, client, editor, bot):
    gh = client()

    async def _pr(*a, **k):
        return {"id": "PR_1"}

    async def _gql(repo, query, variables):
        assert variables == {"id": "PR_1"}
        node = {"diff": "the text", "editor": editor}
        return {"node": {"userContentEdits": {"nodes": [node]}}}

    monkeypatch.setattr(gh, "_pr", _pr)
    monkeypatch.setattr(gh, "_gql", _gql)

    assert await gh.mr_description_history(repo=tmp_path, branch="b") == (
        forge.DescriptionRevision("the text", bot),
    )


KRAFT, EDITED = "Kraft's text.", "A save on top of it."


def gl_note(action: str | None, bot: bool = False, text: str | None = None, at: int = 0) -> dict:
    """A note as `glab._DESCRIPTION_NOTES_QUERY` asks for it (GitLab's GraphQL
    `Note`): a comment when `action` is None, else a system note; `at` is its minute."""
    version = None if text is None else {"id": "gid://gitlab/DescriptionVersion/1"}
    meta = action and {
        "action": action,
        "descriptionVersion": version and {**version, "description": text},
    }
    return {
        "system": action is not None,
        "createdAt": f"2026-11-24T10:{at:02d}:00Z",
        "author": {"bot": bot},
        "systemNoteMetadata": meta,
    }


def gl_page(*notes, after: str | None = None) -> dict:
    """One page of a merge request's notes; `after` is the next page's cursor."""
    info = {"hasNextPage": after is not None, "endCursor": after}
    return {"mergeRequest": {"notes": {"pageInfo": info, "nodes": list(notes)}}}


#: Kraft's own write, saved by the person whose token Kraft runs on.
_OURS = gl_note("description", False, KRAFT, 1)
_BOT = gl_note("description", True, EDITED, 4)


@pytest.mark.parametrize(
    "pages, live, regenerated",
    [
        ([gl_page(_OURS, _BOT)], EDITED, True),
        ([gl_page(_OURS, gl_note("description", False, EDITED, 2))], EDITED, False),
        ([gl_page(_OURS, gl_note("task", True, at=2))], EDITED, True),
        ([gl_page(_OURS, gl_note("task", False, at=2))], EDITED, False),
        (
            [
                gl_page(_OURS, gl_note(None, at=2), gl_note("commit", at=3), after="c1"),
                gl_page(_BOT),
            ],
            EDITED,
            True,
        ),
        ([gl_page({**_OURS, "createdAt": "2026-11-24T11:00:00Z"}, _BOT)], EDITED, False),
        ([gl_page(_OURS, gl_note("description", True, None, 2))], EDITED, False),
        ([gl_page(_OURS, gl_note("description", True, "", 2))], "", False),
        ([gl_page(_OURS, {**gl_note("x", at=2), "systemNoteMetadata": None})], KRAFT, False),
        ([gl_page(_OURS, after="c1"), gl_page(_BOT, after="c1")], EDITED, False),
        (
            [gl_page(_OURS, after=f"c{i + 1}") for i in range(forge.mr.BOT_PAGES)]
            + [gl_page(_BOT)],
            EDITED,
            False,
        ),
        ([{"mergeRequest": {"notes": {"nodes": [_OURS, _BOT]}}}], EDITED, False),
        ([{"mergeRequest": None}], KRAFT, False),
    ],
    ids=[
        "bot-save",
        "person-save",
        "bot-ticks-a-box",
        "person-ticks-a-box",
        "second-page-past-comments-and-other-notes",
        "notes-out-of-order",
        "version-gone",
        "version-empty",
        "system-note-without-metadata",
        "cursor-does-not-move",
        "more-pages-than-it-reads",
        "page-without-page-info",
        "no-merge-request",
    ],
)
async def test_gitlab_reads_who_saved_each_description(
    monkeypatch, tmp_path, pages, live, regenerated
):
    """From GraphQL notes, newest first; anything it cannot vouch for reads as a
    person's save, so the description is kept. Both clients send the same request
    (test_forge_parity.py), so the CLI's stands for both here."""
    gl = forge.GlabCli()
    sent: list[dict] = []

    async def _mr(repo, branch=""):
        return {"iid": 54, "id": 901}

    class _Gql:
        async def graphql(self, query, variables=None):
            sent.append(dict(variables or {}))
            return pages[len(sent) - 1]

    monkeypatch.setattr(gl, "_mr", _mr)
    monkeypatch.setattr(gl, "_transport", lambda repo: _Gql())

    history = await gl.mr_description_history(repo=tmp_path, branch="b")

    assert forge.mr.only_bots_since(live, history, {forge.mr.body_hash(KRAFT)}) is regenerated
    assert [v.get("after") for v in sent] == [None, *(f"c{i + 1}" for i in range(len(sent) - 1))]
    assert {v["id"] for v in sent} == {"gid://gitlab/MergeRequest/901"}
