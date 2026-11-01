"""GlabCli and the GitLab merge train: whether a project merges through one,
where a merge request stands on it, why GitLab dropped it, and queueing through
the train API. Against a stubbed `glab` on PATH, as test_glab.py."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from kraft.adapters import forge

from .nodes import FAIL, NOT_FOUND, Api, FailWith
from .outputs import glab_mr, glab_pipeline

_MR_URL = "https://gitlab.com/o/r/-/merge_requests/7"
_REMOVED = "removed this merge request from the merge train because "
TRAIN_PROJECT = '{"id":1,"merge_trains_enabled":true,"merge_pipelines_enabled":true}'
_TRAIN_CAR = "api projects/:id/merge_trains/merge_requests/7"
_TRAIN_POST = _TRAIN_CAR
_TRAIN_NOTES = "api projects/:id/merge_requests/7/notes"
_TRAIN_PIPELINE = {
    "id": 2826926700,
    "sha": "f" * 40,
    "ref": "refs/merge-requests/7/train",
    "status": "failed",
    "web_url": "https://gitlab.com/o/r/-/pipelines/2826926700",
}


def _train_view(state: str = "opened", head_pipeline: dict | None = None, **fields) -> str:
    return json.dumps(
        {
            "iid": 7,
            "state": state,
            "head_pipeline": head_pipeline,
            "web_url": _MR_URL,
            **fields,
        }
    )


def _notes(*bodies: str) -> str:
    """System notes as GitLab lists them, newest first."""
    return json.dumps([{"id": 20 - n, "system": True, "body": b} for n, b in enumerate(bodies)])


def _note(body: str, *, system: bool = True) -> str:
    return json.dumps([{"id": 9, "system": system, "body": body}])


def _dropped(
    note: str | None,
    head_pipeline: dict | None = _TRAIN_PIPELINE,
    *,
    notes: str | None = None,
    state: str = "opened",
    **view,
) -> dict:
    """A train project whose car for !7 is gone (404), with `note` the newest
    note on the merge request (or `notes`, the whole list as GitLab sends it)
    and `view` more of the merge request's own fields."""
    return {
        "api projects/:id": TRAIN_PROJECT,
        **glab_mr(_train_view(state, head_pipeline, **view)),
        _TRAIN_CAR: NOT_FOUND,
        _TRAIN_NOTES: notes or (_note(note) if note is not None else "[]"),
        **glab_pipeline(),
    }


def _car(status: str) -> dict:
    return {
        "api projects/:id": TRAIN_PROJECT,
        **glab_mr(_train_view()),
        _TRAIN_CAR: json.dumps(
            {"status": status, "pipeline": {"ref": "refs/merge-requests/7/train"}}
        ),
    }


@pytest.mark.parametrize(
    "routes, expected, failure_state",
    [
        pytest.param(
            {"api projects/:id": '{"id":1}'}, forge.QueueRead(), None, id="no-train-field-absent"
        ),
        pytest.param(
            {"api projects/:id": Api(403, '{"message": "403 Forbidden"}')},
            forge.QueueRead(),
            None,
            id="no-train-403",
        ),
        pytest.param(
            {"api projects/:id": '{"merge_trains_enabled":false,"merge_pipelines_enabled":true}'},
            forge.QueueRead(),
            None,
            id="no-train-disabled",
        ),
        pytest.param(
            {"api projects/:id": '{"merge_trains_enabled":true,"merge_pipelines_enabled":false}'},
            forge.QueueRead(),
            None,
            id="no-train-without-merged-results-pipelines",
        ),
        pytest.param(
            {**_car("fresh"), _TRAIN_CAR: Api(403, '{"message": "403 Forbidden"}')},
            forge.QueueRead(),
            None,
            id="no-train-car-endpoint-403",
        ),
        pytest.param(
            _car("fresh"),
            forge.QueueRead(kind="train", state="queued", url=_MR_URL),
            None,
            id="queued-fresh",
        ),
        pytest.param(
            _car("idle"),
            forge.QueueRead(kind="train", state="queued", url=_MR_URL),
            None,
            id="queued-idle",
        ),
        pytest.param(
            _car("merging"),
            forge.QueueRead(kind="train", state="merging", url=_MR_URL),
            None,
            id="merging",
        ),
        pytest.param(
            _car("skip_merged"),
            forge.QueueRead(kind="train", state="merged", url=_MR_URL),
            None,
            id="merged",
        ),
        pytest.param(
            _dropped(_REMOVED + "the pipeline did not succeed"),
            forge.QueueRead(
                kind="train",
                state="dropped",
                cause="code",
                reason="the pipeline did not succeed",
                url=_TRAIN_PIPELINE["web_url"],
            ),
            "failed",
            id="dropped-pipeline-failed",
        ),
        pytest.param(
            _dropped(_REMOVED + "the merge request has conflicts"),
            forge.QueueRead(
                kind="train",
                state="dropped",
                cause="conflict",
                reason="the merge request has conflicts",
                url=_TRAIN_PIPELINE["web_url"],
            ),
            "failed",
            id="dropped-conflict",
        ),
        pytest.param(
            _dropped(
                _REMOVED + "the pipeline did not succeed",
                head_pipeline={**_TRAIN_PIPELINE, "ref": "refs/merge-requests/7/merge"},
            ),
            forge.QueueRead(
                kind="train",
                state="dropped",
                cause="other",
                reason="the pipeline did not succeed",
                url=_MR_URL,
            ),
            None,
            id="dropped-the-head-pipeline-is-not-the-train-s",
        ),
        pytest.param(
            _dropped(
                None,
                notes=json.dumps(
                    [
                        {"system": False, "body": _REMOVED + "a person typed this"},
                        {"system": True, "body": _REMOVED + "the pipeline did not succeed"},
                    ]
                ),
            ),
            forge.QueueRead(
                kind="train",
                state="dropped",
                cause="code",
                reason="the pipeline did not succeed",
                url=_TRAIN_PIPELINE["web_url"],
            ),
            "failed",
            id="a-person-s-comment-is-not-a-system-note",
        ),
        pytest.param(
            _dropped(_REMOVED + "the pipeline did not succeed", state="closed"),
            forge.QueueRead(kind="train", url=_MR_URL),
            None,
            id="closed-is-not-dropped",
        ),
        # `locked` is GitLab merging it: the car is gone, the merge is not.
        pytest.param(
            _dropped(_REMOVED + "the pipeline did not succeed", state="locked"),
            forge.QueueRead(kind="train", state="merging", url=_MR_URL),
            None,
            id="locked-is-merging-not-dropped",
        ),
        # GitLab's reason need not say "conflict"; the merge request does.
        *(
            pytest.param(
                _dropped(_REMOVED + "the merge request is not mergeable", **view),
                forge.QueueRead(
                    kind="train",
                    state="dropped",
                    cause=cause,
                    reason="the merge request is not mergeable",
                    url=_TRAIN_PIPELINE["web_url"],
                ),
                "failed",
                id=f"not-mergeable-{id}",
            )
            for view, cause, id in [
                ({"has_conflicts": True}, "conflict", "has-conflicts"),
                ({"detailed_merge_status": "conflict"}, "conflict", "conflict-status"),
                ({"has_conflicts": False, "detailed_merge_status": "mergeable"}, "code", "clean"),
            ]
        ),
        pytest.param(
            _dropped(
                _REMOVED + "the pipeline was cancelled",
                head_pipeline=None,
            ),
            forge.QueueRead(
                kind="train",
                state="dropped",
                cause="other",
                reason="the pipeline was cancelled",
                url=_MR_URL,
            ),
            None,
            id="dropped-no-train-pipeline-to-blame",
        ),
        pytest.param(
            _dropped("removed this merge request from the merge train"),
            forge.QueueRead(
                kind="train",
                state="dropped",
                cause="other",
                reason="removed this merge request from the merge train",
                url=_TRAIN_PIPELINE["web_url"],
            ),
            "failed",
            id="cancelled-by-person",
        ),
        pytest.param(
            _dropped("aborted automatic add to merge train because the pipeline failed", None),
            forge.QueueRead(
                kind="train",
                state="dropped",
                cause="not_added",
                reason="the pipeline failed",
                url=_MR_URL,
            ),
            None,
            id="never-added",
        ),
        pytest.param(
            _dropped("cancelled automatic add to merge train", None),
            forge.QueueRead(
                kind="train",
                state="dropped",
                cause="other",
                reason="cancelled automatic add to merge train",
                url=_MR_URL,
            ),
            None,
            id="a-person-cancelled-the-add",
        ),
        # Newest first, a note that it joined the train before any leaving
        # note: GitLab has not written why this entry left, and the older
        # removal is an earlier drop's. No reason yet, so a wait.
        *(
            pytest.param(
                _dropped(
                    None,
                    notes=_notes(joined, _REMOVED + "the pipeline did not succeed"),
                ),
                forge.QueueRead(
                    kind="train",
                    state="dropped",
                    cause="other",
                    reason="",
                    url=_TRAIN_PIPELINE["web_url"],
                ),
                "failed",
                id=f"stale-removal-before-{id}",
            )
            for joined, id in [
                ("started a merge train", "started"),
                ("added this merge request to the merge train at position 2", "added"),
                (
                    "enabled automatic add to merge train when the pipeline for abc123 succeeds",
                    "auto-add",
                ),
            ]
        ),
        pytest.param(
            _dropped(
                None,
                notes=_notes(
                    _REMOVED + "the pipeline did not succeed",
                    "started a merge train",
                    "removed this merge request from the merge train",
                ),
            ),
            forge.QueueRead(
                kind="train",
                state="dropped",
                cause="code",
                reason="the pipeline did not succeed",
                url=_TRAIN_PIPELINE["web_url"],
            ),
            "failed",
            id="this-entry-s-removal-after-its-enqueue",
        ),
        # GitLab has not written its system note yet: no reason, which the
        # merge node reads as "look again next poll", never as a person's.
        pytest.param(
            _dropped(None),
            forge.QueueRead(
                kind="train",
                state="dropped",
                cause="other",
                reason="",
                url=_TRAIN_PIPELINE["web_url"],
            ),
            "failed",
            id="no-note-yet",
        ),
    ],
)
async def test_merge_queue_reads_the_train(cli, tmp_path, routes, expected, failure_state):
    cli.stub("glab", routes=routes, default=FAIL)

    got = await forge.GlabCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="main")

    assert got.failure is None or got.failure.state == failure_state
    assert (got.failure is not None) == (failure_state is not None)
    assert replace(got, failure=None) == expected
    if expected.kind == "none" and "403" not in str(routes.get(_TRAIN_CAR)):
        assert cli.calls("glab") == ["api projects/:id -X GET -i"], (
            "asked more of a repo with no train"
        )


def _iid_routes(iid: int, car) -> dict:
    return {
        "api projects/:id": TRAIN_PROJECT,
        **glab_mr(json.dumps({"iid": iid, "state": "opened", "head_pipeline": None})),
        f"api projects/:id/merge_trains/merge_requests/{iid}": car,
        _TRAIN_NOTES.replace("/7/", f"/{iid}/"): "[]",
    }


@pytest.mark.parametrize("iid", [7, 403, 404, 1404, 4040])
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(Api(500, '{"message": "500 Internal Server Error"}'), id="500"),
        pytest.param(FailWith("glab: dial tcp: connection refused"), id="network"),
        pytest.param("timeout", id="timeout"),
    ],
)
async def test_merge_queue_does_not_read_a_failed_call_as_a_drop(
    cli, tmp_path, monkeypatch, iid, failure
):
    """The car call's own argv holds the iid, and a ForgeError's text holds the
    argv: an iid that contains 404 (or 403) must not make a 500, a dead network
    or a timeout read as a drop (or as no train). Reading it wrongly would have
    Kraft re-queue a merge request still on the train (Kraft-l98h6)."""
    car = FAIL if failure == "timeout" else failure
    cli.stub("glab", routes=_iid_routes(iid, car), default=FAIL)
    if failure == "timeout":
        real = forge.git.run_git

        async def slow_car(repo, args, **kw):
            if args[-1].endswith(f"merge_trains/merge_requests/{iid}"):
                raise forge.ForgeError(f"{' '.join(args)} timed out after 120s")
            return await real(repo, args, **kw)

        monkeypatch.setattr(forge.git, "run_git", slow_car)

    with pytest.raises(forge.ForgeError):
        await forge.GlabCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="main")


@pytest.mark.parametrize("iid", [404, 1404])
@pytest.mark.parametrize(
    "not_found",
    [
        pytest.param(NOT_FOUND, id="404-not-found"),
        pytest.param(
            Api(404, '{"message": "404 Merge Train Merge Request Not Found"}'), id="gitlab-words"
        ),
    ],
)
async def test_merge_queue_reads_the_forges_own_404_as_a_drop_whatever_the_iid(
    cli, tmp_path, iid, not_found
):
    cli.stub("glab", routes=_iid_routes(iid, not_found), default=FAIL)

    got = await forge.GlabCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="main")

    assert (got.state, got.cause) == ("dropped", "other")


async def test_merge_queue_reads_a_merged_merge_request_as_merged_not_dropped(cli, tmp_path):
    """A merged merge request's car is gone too; that is a merge, not a drop."""
    routes = {
        "api projects/:id": TRAIN_PROJECT,
        **glab_mr(_train_view("merged"), open=False),
        _TRAIN_CAR: NOT_FOUND,
    }
    cli.stub("glab", routes=routes, default=FAIL)

    got = await forge.GlabCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="main")

    assert (got.kind, got.state) == ("train", "merged")


async def test_merge_on_a_train_project_posts_to_the_train_with_the_reviewed_sha(cli, tmp_path):
    head = "a" * 40
    cli.stub("git", "0")
    cli.stub("glab", routes={"api projects/:id": TRAIN_PROJECT}, default="{}")

    await forge.GlabCli().merge(repo=tmp_path, branch="kraft/abc", mr=forge.MR(7, ""), head=head)

    assert cli.calls("glab")[-1].split() == [
        "api",
        "projects/:id/merge_trains/merge_requests/7",
        "-X",
        "POST",
        "-i",
        "-f",
        "auto_merge=true",
        "-f",
        f"sha={head}",
    ]
    assert not any("/merge " in c for c in cli.calls("glab")), "went round the train"


@pytest.mark.parametrize(
    "project",
    [
        pytest.param('{"id":1}', id="fields-absent"),
        pytest.param('{"merge_trains_enabled":false,"merge_pipelines_enabled":true}', id="off"),
        pytest.param(NOT_FOUND, id="404"),
        pytest.param(Api(403, '{"message": "403 Forbidden"}'), id="403"),
    ],
)
async def test_merge_off_a_train_project_still_puts_the_merge(cli, tmp_path, project):
    """The project read says plainly there is no train -- or that this token
    may not see one: the merge endpoint, as before trains were read."""
    cli.stub("git", "0")
    cli.stub("glab", routes={"api projects/:id": project, **glab_mr(_train_view())}, default="{}")

    await forge.GlabCli().merge(
        repo=tmp_path, branch="kraft/abc", mr=forge.MR(7, ""), head="a" * 40
    )

    assert cli.calls("glab")[-1] == (
        f"api projects/:id/merge_requests/7/merge -X PUT -i -f sha={'a' * 40}"
    )
    assert not any("merge_trains" in c for c in cli.calls("glab"))


_UNREAD_PROJECT = [
    pytest.param(Api(500, '{"message": "500 Internal Server Error"}'), id="500"),
    pytest.param(FailWith("glab: dial tcp: connection refused"), id="network"),
    pytest.param("timeout", id="timeout"),
    pytest.param("not json", id="unparseable"),
    pytest.param("[]", id="not-the-project"),
]


@pytest.mark.parametrize("project", _UNREAD_PROJECT)
@pytest.mark.parametrize("call", ["merge", "merge_queue"])
async def test_a_project_read_that_failed_is_not_read_as_no_train(
    cli, tmp_path, monkeypatch, call, project
):
    """A transient failure reading `projects/:id` says nothing about a train.
    Read as none, `merge` would fall back to the plain merge PUT, which before
    GitLab 19.1 can merge round the train: it raises, as a failed
    merge PUT does. `merge_queue` raises too, which the merge node reads as
    "keep waiting"."""
    cli.stub("git", "0")
    cli.stub("glab", routes={"api projects/:id": FAIL if project == "timeout" else project})
    if project == "timeout":
        real = forge.git.run_git

        async def slow_project(repo, args, **kw):
            if args[-1] == "projects/:id":
                raise forge.ForgeError(f"{' '.join(args)} timed out after 120s")
            return await real(repo, args, **kw)

        monkeypatch.setattr(forge.git, "run_git", slow_project)
    cli_ = forge.GlabCli()

    with pytest.raises(forge.ForgeError):
        if call == "merge":
            await cli_.merge(repo=tmp_path, branch="kraft/abc", mr=forge.MR(7, ""), head="a" * 40)
        else:
            await cli_.merge_queue(repo=tmp_path, branch="kraft/abc", base="main")

    assert not any("/merge " in c for c in cli.calls("glab")), "went round the train"


async def test_a_train_post_refused_with_409_is_a_head_that_moved(cli, tmp_path):
    """The sha the train POST names is not the source branch's head."""
    moved = '[{"iid":7,"state":"opened","sha":"%s"}]' % ("b" * 40)
    cli.stub("git", "0")
    cli.stub(
        "glab",
        routes={
            "api projects/:id": TRAIN_PROJECT,
            _TRAIN_POST: Api(409, '{"message": "SHA does not match HEAD of source branch"}'),
            "api*state=all*": moved,
        },
    )

    with pytest.raises(forge.HeadMoved) as caught:
        await forge.GlabCli().merge(
            repo=tmp_path, branch="kraft/abc", mr=forge.MR(7, ""), head="a" * 40
        )

    assert caught.value.current == "b" * 40
