"""GitHub REST/GraphQL response shapes the `gh` backend reads, shared by more
than one forge test file. Built on 2026-10-30 from gh v2.100.0's own source and
test fixtures (not captured from a live call; see the PR body for what still
needs an e2e check) and GitHub's REST/GraphQL docs:

- the statusCheckRollup fragment: `api/query_builder.go:232-265`; its JSON
  export keys: `api/export_pr.go:131-158`; fixtures
  `pkg/cmd/pr/view/fixtures/*.json` and `pkg/cmd/pr/checks/fixtures/*.json`
- the branch lookup query: `pkg/cmd/pr/shared/finder.go:403-411`; the list
  query: `pkg/cmd/pr/shared/lister.go:53-82`
- jobs, steps and the `--log-failed` output: `pkg/cmd/run/shared/test.go:173-196`,
  `pkg/cmd/run/view/view_test.go:2726-2730`
- run lists: `pkg/cmd/run/shared/shared.go:91-112` and the REST docs for
  `GET /repos/{owner}/{repo}/actions/runs`
- the item-scoped reads (`rest_pull`, `rest_label`; 2026-10-30): the REST docs for
  `GET /repos/{owner}/{repo}/pulls[/{n}]` (`merged_at` is null for a closed,
  unmerged pull request) and `GET /repos/{owner}/{repo}/labels`
"""

from __future__ import annotations

import json

URL = "https://github.com/o/r/pull/7"


def gql(data: dict) -> str:
    """A GraphQL 200 body."""
    return json.dumps({"data": data})


def check_run(name: str, conclusion: str | None, **fields) -> dict:
    """A rollup `CheckRun` node as the GraphQL fragment returns it. `run` is the
    Actions workflow run id behind it (None for a check from another app)."""
    run = fields.pop("run", None)
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": "COMPLETED" if conclusion else "IN_PROGRESS",
        "conclusion": conclusion,
        "startedAt": "2026-09-18T00:15:33Z",
        "completedAt": "2026-09-18T00:16:33Z" if conclusion else None,
        "detailsUrl": f"https://github.com/o/r/actions/runs/{run}/job/9" if run else "sweet link",
        "checkSuite": {"workflowRun": {"databaseId": run, "workflow": {"name": "ci"}}}
        if run
        else {"workflowRun": None},
        **fields,
    }


def status_context(context: str, state: str, **fields) -> dict:
    """A rollup `StatusContext` node (a commit status, not a check run)."""
    return {
        "__typename": "StatusContext",
        "context": context,
        "state": state,
        "targetUrl": "http://example.com/details",
        "createdAt": "2026-09-18T00:15:33Z",
        **fields,
    }


def contexts_page(nodes: list[dict], *, next_cursor: str | None = None) -> dict:
    return {
        "pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor},
        "nodes": nodes,
    }


def pull_request(contexts: dict | None = None, *, oid: str = "abc123", **fields) -> dict:
    """A `pullRequest` node as `github_queries.PR_FIELDS` selects it. `contexts`
    is a `contexts_page`; None is a head commit with no rollup at all."""
    rollup = None if contexts is None else {"contexts": contexts}
    return {
        "id": "PR_kwDO7",
        "number": 7,
        "url": URL,
        "state": "OPEN",
        "isDraft": False,
        "body": "",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "reviewDecision": None,
        "headRefOid": oid,
        "headRefName": "kraft/abc",
        "isCrossRepository": False,
        "isMergeQueueEnabled": False,
        "isInMergeQueue": False,
        "autoMergeRequest": None,
        "mergeCommit": None,
        "labels": {"nodes": []},
        "commits": {"nodes": [{"commit": {"oid": oid, "statusCheckRollup": rollup}}]},
        **fields,
    }


def pr_by_number(pr: dict) -> str:
    return gql({"repository": {"pullRequest": pr}})


def pr_by_branch(*prs: dict, default_branch: str = "main") -> str:
    return gql(
        {
            "repository": {
                "pullRequests": {"nodes": list(prs)},
                "defaultBranchRef": {"name": default_branch},
            }
        }
    )


def checks_page(contexts: dict, *, oid: str = "abc123") -> str:
    """The `node(id:)` re-read that pages a rollup's contexts (finder.go:563-618)."""
    commit = {"oid": oid, "statusCheckRollup": {"contexts": contexts}}
    return gql({"node": {"commits": {"nodes": [{"commit": commit}]}}})


#: `Cli.stub` routes for the pull request reads (`github_queries`): the lookup by
#: branch, by number, the paging re-read, and `find_mr`'s list.
READ_BRANCH = "api*graphql*first: 30*"
READ_NUMBER = "api*graphql*pullRequest(number: $n) { id*"
READ_MORE = "api*graphql*node(id: $id)*"
FIND = "api*graphql*states: [OPEN, CLOSED, MERGED]*"


def find_result(*nodes: dict) -> str:
    """What `find_mr`'s list query answers (`gh pr list --head B --state all`)."""
    return gql({"repository": {"pullRequests": {"nodes": list(nodes)}}})


def listed(state: str = "OPEN", **fields) -> dict:
    return {"number": 7, "url": URL, "state": state, **fields}


FIND_MERGED = find_result(listed("MERGED", mergeCommit={"oid": "c0ffee1"}, headRefOid="c0ffee0"))
#: An open pull request with auto-merge enabled: GitHub merges it itself once
#: its checks pass.
FIND_AUTO_MERGE = find_result(
    listed(
        autoMergeRequest={"enabledAt": "2026-09-22T10:00:00Z", "mergeMethod": "SQUASH"},
        headRefOid="f0reign1",
    )
)

#: The pull requests `ci_status` reads the merge state of.
CONFLICT_PR = pull_request(
    contexts_page([check_run("build", "SUCCESS")]),
    mergeable="CONFLICTING",
    mergeStateStatus="DIRTY",
)
NEEDS_APPROVAL_PR = pull_request(
    contexts_page([check_run("build", "SUCCESS")]),
    mergeable="UNKNOWN",
    mergeStateStatus="BLOCKED",
    reviewDecision="REVIEW_REQUIRED",
)


def merge_state(
    state: str = "CLEAN",
    *,
    queue: bool = False,
    queued: bool = False,
    by_branch: bool = False,
    pr_state: str = "OPEN",
) -> str:
    """What the merge reads before it picks a mutation (merge.go:172, 194-196),
    looked up by number or, `by_branch`, by its head branch."""
    pull = pull_request(
        None,
        state=pr_state,
        mergeStateStatus=state,
        isMergeQueueEnabled=queue,
        isInMergeQueue=queued,
    )
    return pr_by_branch(pull) if by_branch else pr_by_number(pull)


#: What either merge mutation answers (`PullRequestMerge`, `PullRequestAutoMerge`).
MERGED = gql({"mergePullRequest": {"pullRequest": {"id": "PR_kwDO7"}}})
AUTO_MERGE_ENABLED = gql({"enablePullRequestAutoMerge": {"pullRequest": {"id": "PR_kwDO7"}}})

#: GitHub's GraphQL error for an `expectedHeadOid` that is not the head: HTTP 200
#: with `errors[]`. The wording is GitHub's (not in gh's source; see the PR body).
HEAD_MODIFIED = json.dumps(
    {
        "data": {"mergePullRequest": None},
        "errors": [
            {
                "type": "UNPROCESSABLE",
                "path": ["mergePullRequest"],
                "message": "Head branch was modified. Review and try the merge again.",
            }
        ],
    }
)


def workflow_run(run_id: int, **fields) -> dict:
    """One `workflow_runs[]` entry of `GET /repos/{o}/{r}/actions/runs`, trimmed to
    what the parsers read."""
    return {
        "id": run_id,
        "name": "test",
        "head_branch": "main",
        "head_sha": "deadbeef",
        "event": "push",
        "status": "completed",
        "conclusion": "failure",
        "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
        "created_at": "2026-09-19T05:51:00Z",
        "updated_at": "2026-09-19T05:51:09Z",
        "repository": {"id": 1},
        "head_repository": {"id": 1},
        **fields,
    }


def runs(*rows: dict) -> str:
    return json.dumps({"total_count": len(rows), "workflow_runs": list(rows)})


def job(job_id: int, name: str, conclusion: str | None, steps: list[dict] | None = None) -> dict:
    """One `jobs[]` entry of `GET /repos/{o}/{r}/actions/runs/{id}/jobs` or the
    body of `GET .../actions/jobs/{id}` (gh `FailedJob`, run/shared/test.go:173)."""
    return {
        "id": job_id,
        "run_id": 555,
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "html_url": f"https://github.com/o/r/actions/runs/555/job/{job_id}",
        "started_at": "2026-10-21T10:00:00Z",
        "completed_at": "2026-10-21T10:04:34Z",
        "steps": steps or [],
    }


def step(number: int, name: str, conclusion: str, start: str, end: str) -> dict:
    return {
        "name": name,
        "number": number,
        "status": "completed",
        "conclusion": conclusion,
        "started_at": f"2026-10-21T{start}Z",
        "completed_at": f"2026-10-21T{end}Z",
    }


#: The job gh's tests call "sad job": step 1 passes, step 2 ("quux the barf") fails.
SAD_JOB = job(
    20,
    "sad job",
    "failure",
    [
        step(1, "barf the quux", "success", "10:00:00", "10:00:05"),
        step(2, "quux the barf", "failure", "10:00:05", "10:00:30"),
    ],
)

#: `GET .../actions/jobs/20/logs`: GitHub timestamps every line.
SAD_JOB_LOG = (
    "2026-10-21T10:00:01.1000000Z ##[group]Run barf-the-quux\n"
    "2026-10-21T10:00:02.2000000Z fine\n"
    "2026-10-21T10:00:06.3000000Z log line 1\n"
    "2026-10-21T10:00:07.4000000Z log line 2\n"
    "2026-10-21T10:00:29.5000000Z log line 3\n"
)

#: `gh run view 555 --log-failed --job 20` for the job above (view_test.go:2726-2730):
#: the failed step only, `job<TAB>step<TAB>line`.
SAD_JOB_LOG_FAILED = (
    "sad job\tquux the barf\t2026-10-21T10:00:06.3000000Z log line 1\n"
    "sad job\tquux the barf\t2026-10-21T10:00:07.4000000Z log line 2\n"
    "sad job\tquux the barf\t2026-10-21T10:00:29.5000000Z log line 3\n"
)


def rest_pull(number: int = 7, **fields) -> dict:
    """A pull request as the REST `pulls` endpoints return it, trimmed to what the
    reads use. `merged_at` is None until it merges."""
    return {
        "number": number,
        "html_url": f"https://github.com/o/r/pull/{number}",
        "state": "open",
        "title": "Teach it",
        "body": "why",
        "draft": True,
        "merged_at": None,
        "labels": [{"name": "bug"}],
        "head": {"ref": "kraft/abc", "sha": "abc123"},
        "base": {"ref": "main"},
        **fields,
    }


def rest_label(name: str, *, description: str | None = "", color: str = "d73a4a") -> dict:
    return {"name": name, "description": description, "color": color}
