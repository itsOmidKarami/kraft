"""The GraphQL documents the GitHub backend reads a pull request through, and
the lookup that picks the pull request the way `gh pr view` does (finder.go)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from kraft.adapters.forge import git
from kraft.adapters.forge.models import ForgeError
from kraft.adapters.forge.transport import Transport

#: The one check/status node, selected in the first page and in every later one.
_CONTEXT_NODES = """pageInfo { hasNextPage endCursor }
nodes {
  __typename
  ... on CheckRun {
    name status conclusion startedAt completedAt detailsUrl
    checkSuite { workflowRun { databaseId } }
  }
  ... on StatusContext { context state targetUrl createdAt }
}"""
_CORE = """id number url state isDraft body mergeable mergeStateStatus reviewDecision
headRefOid headRefName isCrossRepository
autoMergeRequest { enabledAt }
mergeCommit { oid }
labels(first: 100) { nodes { name } }"""
_ROLLUP = f"""commits(last: 1) {{ nodes {{ commit {{ oid statusCheckRollup {{
  contexts(first: 100) {{ {_CONTEXT_NODES} }}
}} }} }} }}"""

#: What a reader that only needs the pull request itself selects (number, head,
#: body, labels): no checks.
PR_CORE_FIELDS: str = _CORE
#: Core fields and the head commit's check/status rollup, first page.
PR_FIELDS: str = f"{_CORE}\n{_ROLLUP}"
#: What `merge` reads (merge.go:172-196). The queue fields are asked for on a
#: merge only: older GitHub Enterprise Server lacks them (gh feature-detects).
MERGE_FIELDS: str = f"{_CORE}\nisMergeQueueEnabled isInMergeQueue"

_BY_NUMBER = """
query($owner: String!, $name: String!, $n: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $n) { @FIELDS }
  }
}
"""
#: finder.go:403-411: the newest 30 with this head branch, whatever their state.
_BY_BRANCH = """
query($owner: String!, $name: String!, $b: String!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name }
    pullRequests(headRefName: $b, first: 30, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes { @FIELDS }
    }
  }
}
"""
#: lister.go: what `gh pr list --head B --state all -L 5` asks for.
FIND_QUERY = """
query($owner: String!, $name: String!, $b: String!) {
  repository(owner: $owner, name: $name) {
    pullRequests(
      headRefName: $b, states: [OPEN, CLOSED, MERGED], first: 5,
      orderBy: {field: CREATED_AT, direction: DESC}
    ) {
      nodes { number url state autoMergeRequest { enabledAt } mergeCommit { oid } headRefOid }
    }
  }
}
"""
#: finder.go:563-618: the later pages of the head commit's contexts.
_MORE_CONTEXTS = """
query($id: ID!, $after: String!) {
  node(id: $id) {
    ... on PullRequest {
      commits(last: 1) { nodes { commit { statusCheckRollup {
        contexts(first: 100, after: $after) { @CONTEXTS }
      } } } }
    }
  }
}
""".replace("@CONTEXTS", _CONTEXT_NODES)


def owner_vars(**more: str | int) -> dict[str, str | int]:
    """The variables every repository query carries: gh expands the placeholders."""
    return {"owner": "{owner}", "name": "{repo}", **more}


def rollup_rows(contexts: list[dict]) -> list[dict]:
    """`contexts` as `gh pr view --json statusCheckRollup` exports them: a
    `CheckRun` keeps its keys but for `checkSuite`, which becomes `workflowRunId`
    (None for a check outside Actions); a `StatusContext` keeps its own shape,
    `startedAt` being its `createdAt`. Values are never rewritten."""
    rows: list[dict] = []
    for node in contexts:
        if not isinstance(node, dict):
            continue
        if node.get("__typename") == "StatusContext":
            row = {k: v for k, v in node.items() if k != "createdAt"}
            row["startedAt"] = node.get("createdAt")
        else:
            row = {k: v for k, v in node.items() if k != "checkSuite"}
            run = (node.get("checkSuite") or {}).get("workflowRun") or {}
            row["workflowRunId"] = run.get("databaseId")
        rows.append(row)
    return rows


def _repository(data: dict) -> dict:
    repository = data.get("repository")
    if not isinstance(repository, dict):
        raise ForgeError(f"gh api graphql answered without the repository: {data!r:.200}")
    return repository


def pick_by_branch(nodes: list[dict], branch: str, default_branch: str) -> dict | None:
    """finder.go:422-438: open ones first (stable, so newest first within a
    state), the first that comes from this repository and, when the branch is
    the default branch, is open."""
    for pr in sorted(nodes, key=lambda p: p.get("state") != "OPEN"):
        if pr.get("isCrossRepository"):
            continue
        if pr.get("headRefName") == default_branch and pr.get("state") != "OPEN":
            continue
        return pr
    return None


async def _all_contexts(transport: Transport, pr: dict) -> tuple[str, list[dict]]:
    """The head commit's oid and every context of its rollup, later pages read."""
    nodes = (pr.get("commits") or {}).get("nodes") or [{}]
    commit = nodes[0].get("commit") or {}
    contexts = (commit.get("statusCheckRollup") or {}).get("contexts") or {}
    rows = list(contexts.get("nodes") or [])
    page = contexts.get("pageInfo") or {}
    while page.get("hasNextPage"):
        after = page.get("endCursor")
        data = await transport.graphql(_MORE_CONTEXTS, {"id": pr["id"], "after": after})
        try:
            more = data["node"]["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ForgeError(
                f"gh api graphql answered without the next checks: {data!r:.200}"
            ) from exc
        rows += more.get("nodes") or []
        page = more.get("pageInfo") or {}
        if page.get("endCursor") == after:
            break
    return str(commit.get("oid") or pr.get("headRefOid") or ""), rows


async def read_pr(
    transport: Transport, repo: Path, target: list[str], *, checks: bool = True, fields: str = ""
) -> dict:
    """The pull request `target` names (`mr_ops.target`: a number, a branch, or
    nothing for the checkout's own branch), as the GraphQL node.

    With `checks` the node carries `statusCheckRollup` (a flat `rollup_rows`
    list, every page read) and `headCommitOid`; without, the rollup is not asked
    for at all. `fields` (`MERGE_FIELDS`) selects those instead, with no rollup.
    Raises `ForgeError` when there is no such pull request."""
    checks = checks and not fields
    fields = fields or (PR_FIELDS if checks else PR_CORE_FIELDS)
    if target and target[0].isdigit():
        data = await transport.graphql(
            _BY_NUMBER.replace("@FIELDS", fields), owner_vars(n=int(target[0]))
        )
        pr = _repository(data).get("pullRequest")
        if not isinstance(pr, dict):
            raise ForgeError(f"no pull request #{target[0]} found")
    else:
        branch = (
            target[0]
            if target
            else (await git.run_git(repo, ["git", "symbolic-ref", "--short", "HEAD"])).strip()
        )
        data = await transport.graphql(_BY_BRANCH.replace("@FIELDS", fields), owner_vars(b=branch))
        repository = _repository(data)
        default = (repository.get("defaultBranchRef") or {}).get("name")
        found = (repository.get("pullRequests") or {}).get("nodes") or []
        pr = pick_by_branch([p for p in found if isinstance(p, dict)], branch, default)
        if pr is None:
            raise ForgeError(f"no pull requests found for branch {branch!r}")
    if checks:
        oid, contexts = await _all_contexts(transport, pr)
        pr = {k: v for k, v in pr.items() if k != "commits"}
        pr["headCommitOid"], pr["statusCheckRollup"] = oid, rollup_rows(contexts)
    return pr


#: `GET .../actions/runs` keys onto the names `gh run list --json` gave them,
#: which the parsers read. Values are never rewritten.
_RUN_KEYS = {
    "id": "databaseId",
    "head_sha": "headSha",
    "head_branch": "headBranch",
    "html_url": "url",
    "created_at": "createdAt",
    "updated_at": "updatedAt",
}


def run_rows(data: object) -> list[dict]:
    """The `workflow_runs` of a runs answer, keys renamed as `gh run list` does."""
    runs = data.get("workflow_runs") if isinstance(data, dict) else None
    return [
        {_RUN_KEYS.get(k, k): v for k, v in r.items()} for r in runs or [] if isinstance(r, dict)
    ]


#: shared.go:318-325: the conclusions `gh run view --log-failed` calls failed.
_FAILED_STEP = frozenset({"action_required", "failure", "startup_failure", "timed_out"})
_LOG_TIME = re.compile(r"^\ufeff?(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)")


def _second(stamp: str) -> datetime:
    """`stamp` in UTC, cut to the whole second."""
    return datetime.fromisoformat(stamp).astimezone(UTC).replace(microsecond=0)


def failed_step_log(job: dict, log: str) -> str:
    """`log` (a job's whole log, every line stamped) cut the way `gh run view
    --log-failed` prints it: `job<TAB>step<TAB>line` for the lines of each failed
    step, in step order. A line belongs to a step whose [start, end] seconds
    (both inclusive) hold its stamp. With no failed step to cut by, or no line
    in any window, the whole log goes under `UNKNOWN STEP` (gh's own fallback
    when a step has no log of its own)."""
    lines = [
        (m and _second(m[1] + "+00:00"), line)
        for line in log.splitlines()
        if (m := _LOG_TIME.match(line))
    ]
    windows: list[tuple[str, datetime, datetime]] = []
    for s in sorted(job.get("steps") or [], key=lambda s: s.get("number") or 0):
        if (
            str(s.get("conclusion") or "").lower() in _FAILED_STEP
            and s.get("started_at")
            and s.get("completed_at")
        ):
            windows.append(
                (str(s.get("name") or ""), _second(s["started_at"]), _second(s["completed_at"]))
            )
    out: list[str] = []
    taken: set[int] = set()
    for step, start, end in windows:
        for i, (at, line) in enumerate(lines):
            if i not in taken and start <= at <= end:
                taken.add(i)
                out.append(f"{job.get('name')}\t{step}\t{line}\n")
    if out:
        return "".join(out)
    return "".join(f"{job.get('name')}\tUNKNOWN STEP\t{line}\n" for line in log.splitlines())
