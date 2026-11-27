"""`mr.automated_review` on a real forge (Ruling 171): the item, repository or
task names its reviewers -- each a `bot` login or a `check` -- and the backend
reads each off `gh`/`glab`, stubbed at the CLI. None named expects no
automated review. The task-level result shapes and the scheduler are
test_waits.py and tests/test_waits.py."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from kraft import config
from kraft.adapters import forge
from kraft.automated_review import BotReviewer, CheckReviewer, as_reviewers

from .api_outputs import READ_BRANCH, pr_by_branch, pull_request
from .nodes import FAIL
from .outputs import glab_merged_results, glab_mr

HEAD = "abc123"

GH_PR = pr_by_branch(pull_request(oid=HEAD))
GH_REVIEWS = "api repos/{owner}/{repo}/pulls/7/reviews"
GH_COMMENTS = "api repos/{owner}/{repo}/pulls/7/reviews/51/comments"
GH_RUNS = f"api*commits/{HEAD}/check-runs*check_name=review-bot*"
GH_STATUS = f"api repos/{{owner}}/{{repo}}/commits/{HEAD}/status"

GLAB_MR = json.dumps({"iid": 9, "sha": HEAD})
GLAB_APPROVALS = "api projects/:id/merge_requests/9/approvals"
GLAB_DISCUSSIONS = "api projects/:id/merge_requests/9/discussions"
GLAB_STATUSES = f"api projects/:id/repository/commits/{HEAD}/statuses"

#: A merged results head pipeline built from HEAD: its jobs report on its merge
#: commit, not on HEAD.
MERGED, READ_MERGE_COMMIT, MERGE_COMMIT = glab_merged_results(HEAD)
GLAB_MERGED_STATUSES = (
    f"api projects/{MERGED['project_id']}/repository/commits/{MERGED['sha']}/statuses"
)


def _gh_review(
    state: str, *, commit: str = HEAD, login: str = "coderabbitai[bot]", id: int = 51
) -> dict:
    return {"id": id, "user": {"login": login}, "state": state, "commit_id": commit, "body": ""}


def _glab_note(body: str, *, resolved: bool) -> dict:
    return {
        "notes": [
            {
                "author": {"username": "coderabbitai"},
                "body": body,
                "resolvable": True,
                "resolved": resolved,
                "position": {"new_path": "calc.py", "new_line": 3},
            }
        ]
    }


async def _review(cls, cli, tmp_path, routes, reviewer):
    cli.stub(cls.name, routes=routes, default="{}")
    return await cls.cls().automated_review(
        repo=tmp_path, branch="kraft/w1", reviewers=as_reviewers(reviewer)
    )


GH = type("GH", (), {"name": "gh", "cls": forge.GhCli})
GLAB = type("GLAB", (), {"name": "glab", "cls": forge.GlabCli})
BOT = BotReviewer(bot="coderabbitai")
CHECK = CheckReviewer(check="review-bot")


# --- a bot reviewer ------------------------------------------------------------


@pytest.mark.parametrize(
    "backend, before, after",
    [
        (
            GH,
            # A review on an older head does not count: the bot has not seen
            # this one yet.
            {READ_BRANCH: GH_PR, GH_REVIEWS: json.dumps([_gh_review("COMMENTED", commit="old")])},
            {
                READ_BRANCH: GH_PR,
                GH_REVIEWS: json.dumps([_gh_review("COMMENTED")]),
                GH_COMMENTS: "[]",
            },
        ),
        (
            GLAB,
            {**glab_mr(GLAB_MR), GLAB_APPROVALS: '{"approved_by": []}', GLAB_DISCUSSIONS: "[]"},
            {
                **glab_mr(GLAB_MR),
                GLAB_APPROVALS: json.dumps(
                    {"approved_by": [{"user": {"username": "coderabbitai"}}]}
                ),
                GLAB_DISCUSSIONS: json.dumps([_glab_note("fixed now", resolved=True)]),
            },
        ),
    ],
    ids=["gh", "glab"],
)
async def test_a_bot_review_is_pending_until_the_bot_reviews_the_head_then_clean(
    cli, tmp_path, backend, before, after
):
    assert (await _review(backend, cli, tmp_path, before, BOT)).state == "pending"
    assert (await _review(backend, cli, tmp_path, after, BOT)).state == "clean"


@pytest.mark.parametrize(
    "backend, routes",
    [
        (
            GH,
            {
                READ_BRANCH: GH_PR,
                GH_REVIEWS: json.dumps([_gh_review("CHANGES_REQUESTED")]),
                GH_COMMENTS: json.dumps(
                    [{"path": "calc.py", "line": 3, "body": "rename `x` to `total`"}]
                ),
            },
        ),
        (
            GLAB,
            {
                **glab_mr(GLAB_MR),
                GLAB_APPROVALS: '{"approved_by": []}',
                GLAB_DISCUSSIONS: json.dumps([_glab_note("rename `x` to `total`", resolved=False)]),
            },
        ),
    ],
    ids=["gh", "glab"],
)
async def test_a_bot_s_unresolved_feedback_is_actionable(cli, tmp_path, backend, routes):
    review = await _review(backend, cli, tmp_path, routes, BOT)

    assert review.state == "actionable"
    assert review.findings == ("calc.py:3: rename `x` to `total`",)


@pytest.mark.parametrize(
    "backend, routes, expected",
    [
        # Dismissed, and nothing else from the bot on this head: not reviewed.
        (
            GH,
            {
                READ_BRANCH: GH_PR,
                GH_REVIEWS: json.dumps([_gh_review("DISMISSED")]),
                GH_COMMENTS: json.dumps([{"path": "calc.py", "line": 3, "body": "stale"}]),
            },
            "pending",
        ),
        # Dismissed over an earlier clean review of the same head: the
        # earlier one stands, and the dismissed review's comments are ignored.
        (
            GH,
            {
                READ_BRANCH: GH_PR,
                GH_REVIEWS: json.dumps(
                    [_gh_review("COMMENTED", id=50), _gh_review("DISMISSED", id=51)]
                ),
                GH_COMMENTS: json.dumps([{"path": "calc.py", "line": 3, "body": "stale"}]),
                "api repos/{owner}/{repo}/pulls/7/reviews/50/comments": "[]",
            },
            "clean",
        ),
        # GitLab's withdrawal is resolving the discussion: a resolved note is
        # no feedback, and without an approval nothing is reviewed yet.
        (
            GLAB,
            {
                **glab_mr(GLAB_MR),
                GLAB_APPROVALS: '{"approved_by": []}',
                GLAB_DISCUSSIONS: json.dumps([_glab_note("stale", resolved=True)]),
            },
            "pending",
        ),
    ],
    ids=["gh-dismissed-only", "gh-dismissed-over-an-earlier-review", "glab-resolved-only"],
)
async def test_a_withdrawn_bot_review_does_not_count(cli, tmp_path, backend, routes, expected):
    """Kraft-mlicj: a maintainer dismissing a bot's review is GitHub's "this no
    longer blocks". Its comments must not keep the item in a repair loop."""
    assert (await _review(backend, cli, tmp_path, routes, BOT)).state == expected


@pytest.mark.parametrize(
    "replies, reply_state, expected",
    [
        (True, "COMMENTED", ("calc.py:3: x is unused",)),
        (False, "COMMENTED", ("calc.py:3: still unused",)),
        (True, "CHANGES_REQUESTED", ("calc.py:3: still unused",)),
    ],
    ids=["gh-replies-on", "gh-replies-off", "gh-a-changes-requested-reply"],
)
async def test_a_reply_only_review_is_not_the_bot_s_review_where_kraft_replies(
    cli, tmp_path, replies, reply_state, expected
):
    """Where nothing collects there are no Kraft facts: a blank `COMMENTED` review
    made only of replies is the bot answering a thread, so the review before it
    stands. Only for a bot with replies on, and never a verdict."""
    routes = {
        READ_BRANCH: GH_PR,
        GH_REVIEWS: json.dumps(
            [
                {**_gh_review("CHANGES_REQUESTED", id=50), "body": "fix calc"},
                _gh_review(reply_state, id=51),
            ]
        ),
        "api repos/{owner}/{repo}/pulls/7/reviews/50/comments": json.dumps(
            [{"id": 9, "path": "calc.py", "line": 3, "body": "x is unused"}]
        ),
        GH_COMMENTS: json.dumps(
            [{"id": 10, "path": "calc.py", "line": 3, "body": "still unused", "in_reply_to_id": 9}]
        ),
    }
    reviewer = BotReviewer(bot="coderabbitai", replies=True) if replies else BOT

    assert (await _review(GH, cli, tmp_path, routes, reviewer)).findings == expected


# --- a check reviewer ----------------------------------------------------------


@pytest.mark.parametrize(
    "backend, routes, output",
    [
        (
            GH,
            {
                READ_BRANCH: GH_PR,
                GH_RUNS: json.dumps(
                    {
                        "check_runs": [
                            {
                                "status": "completed",
                                "conclusion": "failure",
                                "output": {"title": "2 issues", "summary": "calc.py:3 unused x"},
                            }
                        ]
                    }
                ),
            },
            "calc.py:3 unused x",
        ),
        (
            GLAB,
            {
                **glab_mr(GLAB_MR),
                GLAB_STATUSES: json.dumps(
                    [
                        {"id": 1, "name": "review-bot", "status": "running", "description": ""},
                        {
                            "id": 2,
                            "name": "review-bot",
                            "status": "failed",
                            "description": "calc.py:3 unused x",
                        },
                    ]
                ),
            },
            "calc.py:3 unused x",
        ),
        (
            GLAB,
            {
                **glab_mr(json.dumps({"iid": 262611, "sha": HEAD, "head_pipeline": MERGED})),
                READ_MERGE_COMMIT: MERGE_COMMIT,
                GLAB_STATUSES: "[]",
                GLAB_MERGED_STATUSES: json.dumps(
                    [
                        {
                            "id": 4,
                            "name": "review-bot",
                            "status": "failed",
                            "description": "calc.py:3 unused x",
                        }
                    ]
                ),
            },
            "calc.py:3 unused x",
        ),
    ],
    ids=["gh", "glab", "glab-on-the-merge-commit"],
)
async def test_a_failed_review_check_is_actionable_with_its_output(
    cli, tmp_path, backend, routes, output
):
    review = await _review(backend, cli, tmp_path, routes, CHECK)

    assert review.state == "actionable"
    assert len(review.findings) == 1 and output in review.findings[0]


@pytest.mark.parametrize(
    "backend, routes",
    [
        (
            GH,
            {
                READ_BRANCH: GH_PR,
                GH_RUNS: json.dumps(
                    {"check_runs": [{"status": "in_progress", "conclusion": None}]}
                ),
            },
        ),
        (
            GLAB,
            {
                **glab_mr(GLAB_MR),
                GLAB_STATUSES: json.dumps(
                    [{"id": 3, "name": "review-bot", "status": "running", "description": ""}]
                ),
            },
        ),
        # A cancelled run is never a verdict (Kraft-zn8me): a review check
        # superseded by a newer push is waited out, not handed to a repair.
        (
            GH,
            {
                READ_BRANCH: GH_PR,
                GH_RUNS: json.dumps(
                    {"check_runs": [{"status": "completed", "conclusion": "cancelled"}]}
                ),
            },
        ),
        (
            GLAB,
            {
                **glab_mr(GLAB_MR),
                GLAB_STATUSES: json.dumps(
                    [{"id": 3, "name": "review-bot", "status": "canceled", "description": ""}]
                ),
            },
        ),
    ],
    ids=["gh", "glab", "gh-cancelled", "glab-canceled"],
)
async def test_a_review_check_still_running_is_pending(cli, tmp_path, backend, routes):
    assert (await _review(backend, cli, tmp_path, routes, CHECK)).state == "pending"


# --- several reviewers ---------------------------------------------------------


_BOT_CLEAN = {GH_REVIEWS: json.dumps([_gh_review("COMMENTED")]), GH_COMMENTS: "[]"}
_BOT_FLAGS = {
    GH_REVIEWS: json.dumps([_gh_review("COMMENTED")]),
    GH_COMMENTS: json.dumps([{"path": "calc.py", "line": 3, "body": "unused x"}]),
}
_CHECK_RUNNING = {GH_RUNS: json.dumps({"check_runs": [{"status": "in_progress"}]})}
_CHECK_PASSED = {
    GH_RUNS: json.dumps({"check_runs": [{"status": "completed", "conclusion": "success"}]})
}


_CHECK_READ_FAILS = {GH_RUNS: FAIL}


@pytest.mark.parametrize(
    "bot, check, state, findings",
    [
        (_BOT_CLEAN, _CHECK_RUNNING, "pending", ()),
        # A finding fails it now: the slower reviewer is not waited for.
        (_BOT_FLAGS, _CHECK_RUNNING, "actionable", ("calc.py:3: unused x",)),
        (_BOT_CLEAN, _CHECK_PASSED, "clean", ()),
        # A reviewer whose read failed does not hide what another found.
        (_BOT_FLAGS, _CHECK_READ_FAILS, "actionable", ("calc.py:3: unused x",)),
    ],
    ids=[
        "one-still-pending",
        "a-finding-does-not-wait",
        "all-clean",
        "a-failed-read-keeps-findings",
    ],
)
async def test_several_reviewers_read_as_one_result(cli, tmp_path, bot, check, state, findings):
    routes = {READ_BRANCH: GH_PR, **bot, **check}
    review = await _review(GH, cli, tmp_path, routes, [BOT, CHECK])

    assert (review.state, review.findings) == (state, findings)


async def test_a_failed_read_without_findings_fails_as_today(cli, tmp_path):
    routes = {READ_BRANCH: GH_PR, **_BOT_CLEAN, **_CHECK_READ_FAILS}

    with pytest.raises(forge.ForgeError):
        await _review(GH, cli, tmp_path, routes, [BOT, CHECK])


# --- no reviewer configured ----------------------------------------------------


@pytest.mark.parametrize("cls", [forge.GhCli, forge.GlabCli], ids=["gh", "glab"])
async def test_a_repository_naming_no_reviewer_settles_clean_and_says_why(run_forge, cli, cls):
    """The default chain declares the task; a repository without a reviewer
    must still walk past it -- and the record says it was not reviewed, not
    that it was reviewed clean."""
    assert await run_forge(cls(), "automated_review", "n1") == ("done", "done")

    assert cli.calls("gh") == cli.calls("glab") == [], "asked a forge nobody configured"
    (event,) = run_forge.events("automated_review_not_configured")
    assert event["repo"] == str(run_forge._tmp_path)


# --- the configuration ---------------------------------------------------------


@pytest.mark.parametrize(
    "block",
    [
        {"bot": "coderabbit", "check": "review-bot"},
        {},
        [{"bot": "a"}, {"bot": "b", "check": "c"}],
    ],
    ids=["both", "neither", "a-bad-entry-in-a-list"],
)
def test_a_reviewer_is_named_exactly_one_way_or_refused_at_load(tmp_path, block):
    repos = tmp_path / "repos.yaml"
    repos.write_text(json.dumps({"repos": [{"path": "/r", "automated_review": block}]}))
    with pytest.raises(config.ConfigError, match="exactly one"):
        config.load_repos(repos)


@pytest.mark.parametrize(
    ("entry", "kind", "refused_at"),
    [
        ({"bot": "x", "check": None}, BotReviewer, None),
        ({"check": "c", "bot": None}, CheckReviewer, None),
        ({"check": "c", "replies": True}, None, ("check", "replies")),
        ({"check": "c", "replies": True, "resolve_fixed": True}, None, ("check", "replies")),
        ({"bot": "x", "replies": True}, BotReviewer, None),
        ({"bot": "x", "replies": False}, BotReviewer, None),
        ({"bot": "x", "replies": True, "resolve_fixed": True}, BotReviewer, None),
        ({"bot": "x", "resolve_fixed": True}, None, ("bot",)),
        ({"bot": "x", "replies": True, "resolve_fixed": False}, BotReviewer, None),
        ({"check": "c", "resolve_fixed": True}, None, ("check", "resolve_fixed")),
        ({"bot": "x", "replies": True, "resolve": "kraft"}, None, ("bot", "resolve")),
    ],
    ids=[
        "stored-bot-with-a-null-check",
        "stored-check-with-a-null-bot",
        "a-check-with-a-key-it-lacks",
        "a-check-with-replies",
        "a-bot-with-replies",
        "a-bot-with-replies-off",
        "a-bot-with-resolve-fixed",
        "a-bot-with-resolve-needs-replies",
        "a-bot-with-resolve-fixed-off",
        "a-check-with-resolve-fixed",
        "a-bot-with-the-old-resolve-key",
    ],
)
def test_a_reviewer_entry_is_a_bot_or_a_check(entry, kind, refused_at):
    """`reviewer-entry-is-a-bot-or-a-check`: every stored list holds the other name as null, and
    loads; a key is checked against the entry's own kind.
    `a-bot-reviewer-entry-takes-replies-and-resolve-fixed`: a bot takes both keys, a check never
    does, `resolve_fixed` needs `replies`, the old `resolve` key is refused and a dump leaves a
    key out at False."""
    if kind is None:
        with pytest.raises(ValidationError) as refused:
            as_reviewers([entry])
        assert refused.value.errors()[0]["loc"] == refused_at
        if refused_at == ("bot",):
            assert "needs `replies: true`" in str(refused.value)
        return
    (read,) = as_reviewers([entry])
    assert type(read) is kind
    assert read.model_dump() == {k: v for k, v in entry.items() if v not in (None, False)}
    assert (read.bot, read.check) == (entry.get("bot"), entry.get("check"))


def test_the_schema_offers_replies_and_resolve():
    """VS Code suggests both keys of a bot entry, and neither on a check."""
    for mode in ("validation", "serialization"):
        assert {"replies", "resolve_fixed"} <= set(
            BotReviewer.model_json_schema(mode=mode)["properties"]
        )
        assert not {"replies", "resolve_fixed"} & set(
            CheckReviewer.model_json_schema(mode=mode)["properties"]
        )


def test_repos_yaml_loads_a_bot_with_replies_and_resolve(tmp_path):
    repos = tmp_path / "repos.yaml"
    entry = {"bot": "coderabbitai", "replies": True, "resolve_fixed": True}
    repos.write_text(json.dumps({"repos": [{"path": "/r", "automated_review": [entry]}]}))

    [loaded] = config.load_repos(repos)

    assert as_reviewers(loaded.automated_review) == (BotReviewer(**entry),)
