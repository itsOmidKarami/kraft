"""GlabCli's merge request reads and writes over the REST API: the request each
call makes (method, path, every field) and what is parsed back. Against a
stubbed `glab` on PATH, as test_glab.py; `cli.calls("glab")` is one line per
call, so a row names a request exactly."""

from __future__ import annotations

import json

import pytest

from kraft.adapters import forge
from kraft.adapters.forge import glab as glab_mod

from .nodes import FAIL, Api
from .outputs import GLAB_MR_LIST_MERGED, GLAB_MR_VIEW, glab_mr

NO_MR = forge.MR(0, "")
#: The open merge request on `kraft/abc`, then that merge request itself.
_LISTING = "api projects/:id/merge_requests -X GET -i -f source_branch=kraft/abc"
LIST = f"{_LISTING} -f state=opened -F per_page=1"
GET = "api projects/:id/merge_requests/54 -X GET -i"
PUT = "api projects/:id/merge_requests/54 -X PUT -i"
PIPELINE = "api projects/:id/merge_requests/54/pipelines -X POST -i"
TAGGED = json.dumps({"iid": 54, "title": "Teach it", "labels": ["release::minor", "bug"]})


def _titled(title: str) -> str:
    return json.dumps({"iid": 54, "title": title})


@pytest.mark.parametrize(
    "act, view, sent",
    [
        pytest.param(
            lambda g, r: g.close_mr(repo=r, mr=forge.MRRef(54, "u", state="open")),
            GLAB_MR_VIEW,
            [f"{PUT} -f state_event=close"],
            id="close-sends-only-the-state-event",
        ),
        pytest.param(
            lambda g, r: g.reopen_mr(repo=r, mr=forge.MRRef(54, "u", state="closed")),
            GLAB_MR_VIEW,
            [f"{PUT} -f state_event=reopen"],
            id="reopen-sends-only-the-state-event",
        ),
        pytest.param(
            lambda g, r: g.update_mr(repo=r, branch="kraft/abc", body="fresh body"),
            GLAB_MR_VIEW,
            [LIST, GET, f"{PUT} -f description=fresh body"],
            id="update-description",
        ),
        pytest.param(
            lambda g, r: g.mark_ready(repo=r, branch="kraft/abc", mr=forge.MR(54, "u")),
            _titled("Draft: Teach it"),
            [GET, f"{PUT} -f title=Teach it"],
            id="ready-strips-the-draft-prefix",
        ),
        pytest.param(
            lambda g, r: g.mark_ready(repo=r, branch="kraft/abc", mr=NO_MR),
            _titled(" draft:  WIP: Teach it"),
            [LIST, GET, f"{PUT} -f title=Teach it"],
            id="ready-by-branch-strips-any-case-and-repeats",
        ),
        pytest.param(
            lambda g, r: g.mark_ready(repo=r, branch="kraft/abc", mr=forge.MR(54, "u")),
            _titled("Teach it"),
            [GET],
            id="ready-sends-nothing-when-not-a-draft",
        ),
        pytest.param(
            lambda g, r: g.set_labels(
                repo=r, branch="kraft/abc", mr=forge.MR(54, "u"), labels=("release::patch",)
            ),
            _titled("Teach it"),
            [GET, f"{PUT} -f add_labels=release::patch", PIPELINE],
            id="labels-add-then-a-new-pipeline",
        ),
        pytest.param(
            lambda g, r: g.set_labels(
                repo=r, branch="kraft/abc", mr=forge.MR(54, "u"), labels=("release::patch", "x")
            ),
            TAGGED,
            [
                GET,
                f"{PUT} -f add_labels=release::patch,x -f remove_labels=release::minor",
                PIPELINE,
            ],
            id="labels-drop-the-same-scope-label-only",
        ),
        pytest.param(
            lambda g, r: g.set_labels(repo=r, branch="kraft/abc", mr=NO_MR, labels=("a",)),
            _titled("Teach it"),
            [LIST, GET, f"{PUT} -f add_labels=a", PIPELINE],
            id="labels-resolve-the-sentinel-number-and-never-use-it",
        ),
        pytest.param(
            lambda g, r: g.rerun_after_description_change(
                repo=r, branch="kraft/abc", ci=forge.CIStatus(state="failed", url="u")
            ),
            GLAB_MR_VIEW,
            [LIST, GET, PIPELINE],
            id="description-change-creates-a-pipeline-not-a-retry",
        ),
    ],
)
async def test_glab_merge_request_writes_are_these_requests(cli, tmp_path, act, view, sent):
    cli.stub("glab", routes=glab_mr(view), default="{}")

    got = await act(forge.GlabCli(), tmp_path)

    assert got in (None, True), "a rerun reports that it started a new pipeline"

    assert cli.calls("glab") == sent


@pytest.mark.parametrize(
    "meta, lookups, fields",
    [
        pytest.param(forge.mr.MRMeta(), [], "", id="bare-draft-with-no-label-assignee-or-reviewer"),
        pytest.param(
            forge.mr.MRMeta(
                labels=("release::minor", "x"), assignees=("omid",), reviewers=("ada", "grace")
            ),
            [
                "api users -X GET -i -f username=omid",
                "api users -X GET -i -f username=ada",
                "api users -X GET -i -f username=grace",
            ],
            " -f labels=release::minor,x -f assignee_ids=1 -f reviewer_ids=2,3",
            id="labels-and-people-resolved-to-ids",
        ),
    ],
)
async def test_glab_open_mr_posts_a_draft_with_these_fields(cli, tmp_path, meta, lookups, fields):
    """glab's own create (`mr create`, :668-683, :793-801): `Draft: ` on the
    title, the labels one comma list, people as user ids, and no
    remove_source_branch or squash, which Kraft never asks for."""
    cli.stub("git", "")
    cli.stub(
        "glab",
        routes={
            "api*username=omid*": '[{"id":1}]',
            "api*username=ada*": '[{"id":2}]',
            "api*username=grace*": '[{"id":3}]',
        },
        default=GLAB_MR_VIEW,
    )

    mr = await forge.GlabCli().open_mr(
        repo=tmp_path, branch="kraft/abc", base="release", title="Teach it", body="why", meta=meta
    )

    assert (mr.number, mr.url.endswith("/54")) == (54, True)
    assert cli.calls("glab") == [
        *lookups,
        "api projects/:id/merge_requests -X POST -i -f source_branch=kraft/abc "
        "-f target_branch=release -f title=Draft: Teach it -f description=why" + fields,
    ]


async def test_glab_open_mr_refuses_a_user_gitlab_does_not_know(cli, tmp_path):
    cli.stub("git", "")
    cli.stub("glab", routes={"api users": "[]"}, default=GLAB_MR_VIEW)

    with pytest.raises(forge.ForgeError, match="ghost"):
        await forge.GlabCli().open_mr(
            repo=tmp_path,
            branch="kraft/abc",
            base="main",
            title="t",
            body="b",
            meta=forge.mr.MRMeta(assignees=("ghost",)),
        )

    assert not any("-X POST" in c for c in cli.calls("glab")), "created an MR half-described"


async def test_glab_mr_without_a_branch_reads_the_checkouts_current_one(cli, tmp_path):
    cli.stub("git", routes={"symbolic-ref --short": "kraft/xyz"})
    cli.stub(
        "glab",
        routes={**glab_mr('{"iid":54,"description":"the live body"}')},
    )

    assert await forge.GlabCli().mr_body(repo=tmp_path, branch="") == "the live body"

    assert cli.calls("glab")[0] == LIST.replace("kraft/abc", "kraft/xyz")


async def test_glab_mr_body_reads_the_live_description(cli, tmp_path):
    cli.stub("glab", routes=glab_mr('{"iid":54,"description":"the live body"}'))

    assert await forge.GlabCli().mr_body(repo=tmp_path, branch="kraft/abc") == "the live body"
    assert cli.calls("glab") == [LIST, GET]


async def test_merge_state_falls_back_to_find_mr_when_the_mr_read_fails(cli, tmp_path):
    """Kraft-v6ci's reported failure mode: the read of the merge request errors
    when the branch resolves to nothing because the state changed under it. The
    same all-states lookup merge's own "already merged" shortcut trusts
    (Kraft-xron) still finds it."""
    cli.stub("glab", routes={**glab_mr(FAIL), "api*state=all*": GLAB_MR_LIST_MERGED}, default=FAIL)

    assert await forge.GlabCli()._merge_state(tmp_path, branch="kraft/abc") == "merged"


@pytest.mark.parametrize(
    "routes",
    [
        {**glab_mr(FAIL), "api*state=all*": "[]"},
        {"api*state=opened*": "[]", "api*state=all*": "[]"},
    ],
    ids=["the-read-fails", "no-open-merge-request"],
)
async def test_merge_state_still_raises_when_find_mr_has_no_answer(cli, tmp_path, routes):
    """The fallback must not turn a genuine outage or auth failure into a false
    "merged": it only fires when `find_mr` itself confirms the merge."""
    cli.stub("glab", routes=routes, default=FAIL)

    with pytest.raises(forge.ForgeError):
        await forge.GlabCli()._merge_state(tmp_path, branch="kraft/abc")


@pytest.mark.parametrize(
    "mr_list, expected",
    [
        # A list, not a view: an empty list is an unambiguous 'no MR', where
        # `view`'s non-zero exit would force the caller to swallow real errors.
        ("[]", None),
        # One branch can carry a closed MR and an open one; every caller means
        # the open one.
        (
            '[{"iid":54,"state":"closed","web_url":"http://x/54"},'
            '{"iid":62,"state":"opened","web_url":"http://x/62"}]',
            forge.MRRef(number=62, url="http://x/62", state="open"),
        ),
        # Kraft-n60oh: with no merge commit, a squash fast-forwarded onto the
        # target is what landed; with neither, the head itself.
        (
            '[{"iid":54,"state":"merged","web_url":"http://x/54","sha":"c3",'
            '"merge_commit_sha":null,"squash_commit_sha":"c2"}]',
            forge.MRRef(number=54, url="http://x/54", state="merged", merged_sha="c2"),
        ),
        (
            '[{"iid":54,"state":"merged","web_url":"http://x/54","sha":"c3",'
            '"merge_commit_sha":null,"squash_commit_sha":null}]',
            forge.MRRef(number=54, url="http://x/54", state="merged", merged_sha="c3"),
        ),
    ],
    ids=[
        "none-when-the-branch-has-none",
        "prefers-the-open-one",
        "a-fast-forwarded-squash-landed-its-squash",
        "a-fast-forward-landed-its-head",
    ],
)
async def test_glab_find_mr_picks_the_branch_s_merge_request(cli, tmp_path, mr_list, expected):
    cli.stub("glab", mr_list)

    assert await forge.GlabCli().find_mr(repo=tmp_path, branch="kraft/abc") == expected
    assert cli.calls("glab") == [f"{_LISTING} -f state=all -F per_page=5"], (
        "every state, or a merged MR reads as none; this branch only; one short page"
    )


@pytest.mark.parametrize("branch", ["kraft/abc", ""], ids=["named-branch", "current-branch"])
async def test_glab_reads_a_merged_merge_request_after_the_open_list_comes_back_empty(
    cli, tmp_path, branch
):
    """`glab mr view <branch>` applied no state filter (mrutils.go:321-331), so
    a merge request merged out-of-band stays readable: open first, then any."""
    merged = json.dumps({"iid": 54, "state": "merged", "description": "d"})
    cli.stub("git", routes={"symbolic-ref --short": "kraft/abc"})
    cli.stub("glab", routes=glab_mr(merged, open=False), default=FAIL)

    detail = await forge.GlabCli()._merge_state(tmp_path, branch=branch)

    assert detail == "merged"
    assert cli.calls("glab") == [LIST, f"{_LISTING} -f state=all -F per_page=1", GET]


_MERGE = "api projects/:id/merge_requests/54/merge -X PUT -i"
_HEAD = "a" * 40
_NO_TRAIN = {"api projects/:id": '{"id":1}'}


def _piped(status: str | None, key: str = "head_pipeline") -> str:
    pipeline = {"id": 9, "status": status} if status else None
    return json.dumps({"iid": 54, "state": "opened", key: pipeline})


def _draft(status: str | None = None) -> str:
    pipeline = {"id": 9, "status": status} if status else None
    return json.dumps({"iid": 54, "state": "opened", "draft": True, "head_pipeline": pipeline})


def _two(head: str | None, older: str | None) -> str:
    """A merge request carrying both pipeline keys, which may disagree."""
    return json.dumps(
        {
            "iid": 54,
            "state": "opened",
            "head_pipeline": {"id": 9, "status": head} if head else None,
            "pipeline": {"id": 9, "status": older} if older else None,
        }
    )


@pytest.mark.parametrize(
    "view, sent",
    [
        pytest.param(_piped(None), [f"{_MERGE} -f sha={_HEAD}"], id="no-pipeline-merges-directly"),
        pytest.param(
            _piped("running"), [f"{_MERGE} -F auto_merge=true -f sha={_HEAD}"], id="running"
        ),
        pytest.param(
            _piped("pending"), [f"{_MERGE} -F auto_merge=true -f sha={_HEAD}"], id="pending"
        ),
        pytest.param(
            _piped("success"), [f"{_MERGE} -F auto_merge=true -f sha={_HEAD}"], id="succeeded"
        ),
        pytest.param(
            _piped("running", "pipeline"),
            [f"{_MERGE} -F auto_merge=true -f sha={_HEAD}"],
            id="the-older-pipeline-key",
        ),
        *[
            pytest.param(
                _piped(status),
                [f"{_MERGE} -F auto_merge=true -f sha={_HEAD}"],
                id=f"{status}-is-no-reason-to-hold-back",
            )
            for status in ("created", "scheduled", "manual", "skipped")
        ],
        pytest.param(
            _two("running", "failed"),
            [f"{_MERGE} -F auto_merge=true -f sha={_HEAD}"],
            id="head_pipeline-wins-over-the-older-key",
        ),
        pytest.param(
            _two(None, "failed"), [], id="the-older-key-when-head_pipeline-is-null-raises-on-it"
        ),
        pytest.param(_piped("failed"), [], id="failed-sends-nothing"),
        pytest.param(_piped("canceled"), [], id="canceled-sends-nothing"),
        pytest.param(_draft(), [], id="a-draft-sends-nothing"),
        pytest.param(_draft("running"), [], id="a-draft-with-a-running-pipeline-sends-nothing"),
    ],
)
async def test_glab_merge_puts_the_fields_glab_sends_for_the_head_pipeline(
    cli, tmp_path, view, sent
):
    """mr_merge.go:221-231: `auto_merge` (never `merge_when_pipeline_succeeds`)
    whenever a head pipeline exists, `sha` always, nothing else; a failed or
    canceled pipeline is no request at all."""
    cli.stub("git", "0")
    cli.stub("glab", routes={**_NO_TRAIN, **glab_mr(view)}, default="{}")
    merging = forge.GlabCli().merge(
        repo=tmp_path, branch="kraft/abc", mr=forge.MR(54, ""), head=_HEAD
    )

    if sent:
        await merging
    else:
        with pytest.raises(forge.ForgeError, match="!54: (its pipeline|still a draft)"):
            await merging

    assert [c for c in cli.calls("glab") if "/merge " in c] == sent


@pytest.mark.parametrize(
    "refusal, forge_head, head_moved",
    [
        pytest.param(
            Api(409, '{"message": "SHA mismatch"}'), "b" * 40, True, id="409-is-a-head-that-moved"
        ),
        pytest.param(
            Api(409, '{"message": "conflict"}'),
            _HEAD,
            False,
            id="409-at-the-reviewed-head-stays-a-forge-error",
        ),
        pytest.param(
            Api(405, '{"message": "Method Not Allowed"}'),
            "b" * 40,
            False,
            id="405-stays-a-forge-error",
        ),
    ],
)
async def test_glab_merge_409_is_a_head_that_moved_and_names_the_forges(
    cli, tmp_path, refusal, forge_head, head_moved
):
    moved = json.dumps([{"iid": 54, "state": "opened", "sha": forge_head}])
    cli.stub(
        "glab",
        routes={
            **_NO_TRAIN,
            "api projects/:id/merge_requests/54/merge": refusal,
            "api*state=all*": moved,
            **glab_mr(_piped("running")),
        },
        default="{}",
    )
    cli.stub("git", "0")

    with pytest.raises(forge.ForgeError) as caught:
        await forge.GlabCli().merge(
            repo=tmp_path, branch="kraft/abc", mr=forge.MR(54, ""), head=_HEAD
        )

    assert isinstance(caught.value, forge.HeadMoved) is head_moved
    if head_moved:
        assert caught.value.current == forge_head
    else:
        assert caught.value.status == refusal.status


async def test_glab_merge_names_the_branch_when_no_number_is_known(cli, tmp_path):
    """`run_task` threads no number (0): the branch resolves the merge request,
    and the PUT never targets `merge_requests/0`."""
    cli.stub("git", "0")
    cli.stub("glab", routes={**_NO_TRAIN, **glab_mr(_piped(None))}, default="{}")

    await forge.GlabCli().merge(repo=tmp_path, branch="kraft/abc", mr=NO_MR, head=_HEAD)

    assert cli.calls("glab")[-3:] == [LIST, GET, f"{_MERGE} -f sha={_HEAD}"]


@pytest.mark.parametrize(
    "version, field",
    [
        pytest.param(
            "17.10.4", "merge_when_pipeline_succeeds", id="17.10-has-only-the-older-field"
        ),
        pytest.param("17.11.0", "auto_merge", id="17.11"),
        pytest.param("19.10.1-ee", "auto_merge", id="19.10-not-a-string-compare"),
        pytest.param("not json", "auto_merge", id="unreadable-as-glab"),
    ],
)
async def test_glab_merge_names_the_auto_merge_field_by_the_gitlab_version(
    cli, tmp_path, version, field
):
    body = version if version == "not json" else json.dumps({"version": version})
    cli.stub("git", "0")
    cli.stub(
        "glab",
        routes={**_NO_TRAIN, "api version": body, **glab_mr(_piped("running"))},
        default="{}",
    )

    await forge.GlabCli().merge(repo=tmp_path, branch="kraft/abc", mr=forge.MR(54, ""), head=_HEAD)

    assert [c for c in cli.calls("glab") if "/merge " in c] == [
        f"{_MERGE} -F {field}=true -f sha={_HEAD}"
    ]


async def test_glab_reads_the_version_once_for_a_merge_and_its_cancel(cli, tmp_path):
    cli.stub("git", "0")
    cli.stub(
        "glab",
        routes={**_NO_TRAIN, "api version": '{"version": "19.5.0"}', **glab_mr(_piped("running"))},
        default="{}",
    )
    glab = forge.GlabCli()

    await glab.merge(repo=tmp_path, branch="kraft/abc", mr=forge.MR(54, ""), head=_HEAD)
    await glab.cancel_merge(
        repo=tmp_path, branch="kraft/abc", mr=forge.MRRef(54, "u", state="open")
    )

    assert cli.calls("glab").count("api version -X GET -i") == 1


_NOT_YET = Api(406, '{"message": "Branch cannot be merged"}')


@pytest.mark.parametrize(
    "answers, requests, outcome",
    [
        pytest.param([_NOT_YET, Api(200, "{}")], 2, None, id="406-then-200-merges"),
        pytest.param([_NOT_YET] * 3, 3, 406, id="406-three-times-raises-with-its-status"),
        pytest.param([Api(409, "{}")], 1, "head_moved", id="409-not-retried"),
        pytest.param([Api(422, "{}")], 1, 422, id="other-errors-not-retried"),
    ],
)
async def test_glab_merge_retries_only_a_406(
    cli, tmp_path, monkeypatch, answers, requests, outcome
):
    """mr_merge.go:247-271: 3 attempts, only HTTP 406 retried. The stub answers
    by route and cannot change its answer per call, so the PUT is scripted."""
    monkeypatch.setattr(glab_mod, "_MERGE_RETRY_DELAY", 0)
    cli.stub("git", "0")
    cli.stub("glab", routes={**_NO_TRAIN, **glab_mr(_piped("running"))}, default="{}")
    real, puts = glab_mod.GlabCli._api, []

    async def scripted(self, repo, path, **kw):
        if not path.endswith("/merge"):
            return await real(self, repo, path, **kw)
        answer = answers[len(puts)]
        puts.append(kw["fields"])
        if answer.status >= 400:
            raise forge.ForgeError("refused", status=answer.status)

    monkeypatch.setattr(glab_mod.GlabCli, "_api", scripted)
    merging = forge.GlabCli().merge(
        repo=tmp_path, branch="kraft/abc", mr=forge.MR(54, ""), head=_HEAD
    )

    if outcome is None:
        await merging
    else:
        with pytest.raises(forge.ForgeError) as caught:
            await merging
        assert (
            isinstance(caught.value, forge.HeadMoved)
            if outcome == "head_moved"
            else caught.value.status == outcome
        )
    assert len(puts) == requests
    assert all(p == {"auto_merge": True, "sha": _HEAD} for p in puts), "the same request each time"


async def test_glab_mr_refuses_to_look_for_a_branch_it_cannot_name(cli, tmp_path):
    cli.stub("git", routes={"symbolic-ref --short": ""})
    cli.stub("glab", routes=glab_mr(GLAB_MR_VIEW))

    with pytest.raises(forge.ForgeError, match="no branch"):
        await forge.GlabCli().mr_body(repo=tmp_path, branch="")

    assert cli.calls("glab") == []


@pytest.mark.parametrize(
    "routes, assignees, expect",
    [
        pytest.param({"api user": '{"id": 77}'}, ("@me",), "assignee_ids=77", id="@me-is-the-user"),
        pytest.param({"api users": "[]"}, ("ghost",), "has 0 users", id="no-match"),
        pytest.param(
            {"api users": '[{"id":1},{"id":2}]'}, ("ada",), "has 2 users", id="two-matches"
        ),
    ],
)
async def test_glab_open_mr_resolves_a_user_as_glab_does(cli, tmp_path, routes, assignees, expect):
    """user.go:16-19 (`@me`) and the one-match rule."""
    cli.stub("git", "")
    cli.stub(
        "glab", routes={**routes, "api projects/:id/merge_requests": GLAB_MR_VIEW}, default="{}"
    )
    opening = forge.GlabCli().open_mr(
        repo=tmp_path,
        branch="kraft/abc",
        base="main",
        title="t",
        body="b",
        meta=forge.mr.MRMeta(assignees=assignees),
    )

    if expect.startswith("assignee_ids"):
        await opening
        assert expect in cli.calls("glab")[-1]
    else:
        with pytest.raises(forge.ForgeError, match=expect):
            await opening
