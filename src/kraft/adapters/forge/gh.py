"""GitHub through `gh`."""

from __future__ import annotations

from pathlib import Path

from kraft.adapters.forge import git
from kraft.adapters.forge import mr as mr_ops
from kraft.adapters.forge.models import MR, CIState, CIStatus, MRRef

_GH_MR_STATES: dict[str, str] = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}


class GhCli:
    """GitHub through `gh`. For the public repo after the v0.1.0 split."""

    async def open_mr(self, *, repo: Path, branch: str, title: str, body: str) -> MR:
        await git.assert_clean(repo)
        await self.push(repo=repo, branch=branch)
        # `--fill` titles the PR from the commits; see GlabCli.open_mr.
        await git.run_git(
            repo, ["gh", "pr", "create", "--title", mr_ops.mr_title(title), "--body", body]
        )
        raw = await git.run_git(repo, ["gh", "pr", "view", "--json", "number,url"])
        data = mr_ops.parse_json(raw, "gh pr view")
        return MR(number=int(data["number"]), url=str(data["url"]))

    async def push(self, *, repo: Path, branch: str) -> None:
        await git.push(repo, branch)

    async def update_mr(self, *, repo: Path, branch: str, body: str) -> None:
        await git.run_git(repo, ["gh", "pr", "edit", "--body", body])

    async def ci_status(self, *, repo: Path, mr: MR, branch: str = "") -> CIStatus:
        # `gh pr view` with no argument already resolves from the current
        # branch, so `branch` is accepted for one Forge shape and unused here.
        # `mergeable,mergeStateStatus` ride along on the call the node already
        # makes: the check node has to know whether the PR can land, and one
        # round trip already carries it (Kraft-ejj9).
        raw = await git.run_git(
            repo,
            [
                "gh",
                "pr",
                "view",
                "--json",
                "number,url,statusCheckRollup,mergeable,mergeStateStatus",
            ],
        )
        data = mr_ops.parse_json(raw, "gh pr view")
        # Either field can carry the bad news: `mergeable` is
        # MERGEABLE/CONFLICTING/UNKNOWN, `mergeStateStatus` adds DIRTY.
        states = (str(data.get("mergeable") or ""), str(data.get("mergeStateStatus") or ""))
        mergeable = mr_ops.mergeable(*states)
        detail = "/".join(s for s in states if s)
        checks = data.get("statusCheckRollup") or []
        if not checks:
            return CIStatus(
                state="pending",
                url=str(data.get("url", "")),
                jobs=("no checks yet",),
                mergeable=mergeable,
                merge_detail=detail,
            )
        jobs = tuple(f"{c.get('name')}: {c.get('conclusion') or 'PENDING'}" for c in checks)
        conclusions = [str(c.get("conclusion") or "") for c in checks]
        if any(c in ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED") for c in conclusions):
            state: CIState = "failed"
        elif all(c in ("SUCCESS", "NEUTRAL") for c in conclusions):
            state = "success"
        else:
            state = "pending"
        return CIStatus(
            state=state,
            url=str(data.get("url", "")),
            jobs=jobs,
            mergeable=mergeable,
            merge_detail=detail,
        )

    async def set_labels(self, *, repo: Path, mr: MR, labels: tuple[str, ...]) -> None:
        """Label the pull request.

        No pipeline to re-create, unlike GitLab: a workflow that cares about
        labels keys on `pull_request: types: [labeled]` and GitHub re-evaluates
        it on the edit.
        """
        if not labels:
            return
        target = [str(mr.number)] if mr.number > 0 else []
        await git.run_git(repo, ["gh", "pr", "edit", *target, "--add-label", ",".join(labels)])

    async def merge(self, *, repo: Path, branch: str, mr: MR) -> None:
        await git._assert_pushed(repo, branch)
        target = [str(mr.number)] if mr.number > 0 else []
        await git.run_git(repo, ["gh", "pr", "merge", *target, "--squash"])

    async def find_mr(self, *, repo: Path, branch: str) -> MRRef | None:
        raw = await git.run_git(
            repo,
            [
                "gh",
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                "all",
                "--json",
                "number,url,state",
                "-L",
                "5",
            ],
        )
        rows = mr_ops.parse_json(raw, "gh pr list")
        return mr_ops.pick_mr(
            [
                MRRef(
                    number=int(r["number"]),
                    url=str(r.get("url", "")),
                    state=_GH_MR_STATES.get(str(r.get("state", "")), "closed"),
                )
                for r in rows
            ]
        )
