"""`mr.automated_review` on a real forge (Ruling 171): the repository names its
reviewer -- a `bot` login or a `check` -- and the backend reads that one
reviewer off `gh`/`glab`, stubbed at the CLI. A repository that names none
expects no automated review. The task-level result shapes and the scheduler are
test_waits.py and tests/test_waits.py."""

from __future__ import annotations

import json

import pytest

from kraft import config
from kraft.adapters import forge
from kraft.automated_review import AutomatedReview

HEAD = "abc123"

GH_PR = json.dumps({"number": 7, "headRefOid": HEAD})
GH_REVIEWS = "api repos/{owner}/{repo}/pulls/7/reviews?per_page=100"
GH_COMMENTS = "api repos/{owner}/{repo}/pulls/7/reviews/51/comments?per_page=100"
GH_RUNS = f"api repos/{{owner}}/{{repo}}/commits/{HEAD}/check-runs?check_name=review-bot"
GH_STATUS = f"api repos/{{owner}}/{{repo}}/commits/{HEAD}/status"

GLAB_MR = json.dumps({"iid": 9, "sha": HEAD})
GLAB_APPROVALS = "api projects/:id/merge_requests/9/approvals"
GLAB_DISCUSSIONS = "api projects/:id/merge_requests/9/discussions?per_page=100"
GLAB_STATUSES = f"api projects/:id/repository/commits/{HEAD}/statuses?per_page=100"


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
    return await cls.cls().automated_review(repo=tmp_path, branch="kraft/w1", reviewer=reviewer)


GH = type("GH", (), {"name": "gh", "cls": forge.GhCli})
GLAB = type("GLAB", (), {"name": "glab", "cls": forge.GlabCli})
BOT = AutomatedReview(bot="coderabbitai")
CHECK = AutomatedReview(check="review-bot")


# --- a bot reviewer ------------------------------------------------------------


@pytest.mark.parametrize(
    "backend, before, after",
    [
        (
            GH,
            # A review on an older head does not count: the bot has not seen
            # this one yet.
            {"pr view": GH_PR, GH_REVIEWS: json.dumps([_gh_review("COMMENTED", commit="old")])},
            {
                "pr view": GH_PR,
                GH_REVIEWS: json.dumps([_gh_review("COMMENTED")]),
                GH_COMMENTS: "[]",
            },
        ),
        (
            GLAB,
            {"mr view": GLAB_MR, GLAB_APPROVALS: '{"approved_by": []}', GLAB_DISCUSSIONS: "[]"},
            {
                "mr view": GLAB_MR,
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
                "pr view": GH_PR,
                GH_REVIEWS: json.dumps([_gh_review("CHANGES_REQUESTED")]),
                GH_COMMENTS: json.dumps(
                    [{"path": "calc.py", "line": 3, "body": "rename `x` to `total`"}]
                ),
            },
        ),
        (
            GLAB,
            {
                "mr view": GLAB_MR,
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
                "pr view": GH_PR,
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
                "pr view": GH_PR,
                GH_REVIEWS: json.dumps(
                    [_gh_review("COMMENTED", id=50), _gh_review("DISMISSED", id=51)]
                ),
                GH_COMMENTS: json.dumps([{"path": "calc.py", "line": 3, "body": "stale"}]),
                "api repos/{owner}/{repo}/pulls/7/reviews/50/comments?per_page=100": "[]",
            },
            "clean",
        ),
        # GitLab's withdrawal is resolving the discussion: a resolved note is
        # no feedback, and without an approval nothing is reviewed yet.
        (
            GLAB,
            {
                "mr view": GLAB_MR,
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


# --- a check reviewer ----------------------------------------------------------


@pytest.mark.parametrize(
    "backend, routes, output",
    [
        (
            GH,
            {
                "pr view": GH_PR,
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
                "mr view": GLAB_MR,
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
    ],
    ids=["gh", "glab"],
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
                "pr view": GH_PR,
                GH_RUNS: json.dumps(
                    {"check_runs": [{"status": "in_progress", "conclusion": None}]}
                ),
            },
        ),
        (
            GLAB,
            {
                "mr view": GLAB_MR,
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
                "pr view": GH_PR,
                GH_RUNS: json.dumps(
                    {"check_runs": [{"status": "completed", "conclusion": "cancelled"}]}
                ),
            },
        ),
        (
            GLAB,
            {
                "mr view": GLAB_MR,
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
    [{"bot": "coderabbit", "check": "review-bot"}, {}],
    ids=["both", "neither"],
)
def test_a_reviewer_is_named_exactly_one_way_or_refused_at_load(tmp_path, block):
    repos = tmp_path / "repos.yaml"
    repos.write_text(json.dumps({"repos": [{"path": "/r", "automated_review": block}]}))
    with pytest.raises(config.ConfigError, match="exactly one"):
        config.load_repos(repos, validate_steering=False)
