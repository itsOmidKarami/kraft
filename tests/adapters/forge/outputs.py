"""Real `glab`/`gh` output shapes, shared by more than one forge test file.
Captured from glab 1.116.0-1.117.0 and gh 2.100.0 against this repo on
2026-09-07/09; parsers are written against these, not against recollection.
Shapes only one file uses live in that file."""

import json

GLAB_MR_VIEW = (
    '{"iid":54,"target_branch":"main","source_branch":"kraft/abc","state":"opened",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}'
)

# `glab mr view -F json` for a branch that conflicts with main.
# `detailed_merge_status` says *why*, `merge_status` is the older, coarser one.
GLAB_MR_VIEW_CONFLICT = (
    '{"iid":54,"state":"opened","source_branch":"kraft/abc",'
    '"merge_status":"cannot_be_merged","detailed_merge_status":"conflict",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}'
)

# Blocked on a required approval, not a conflict.
GLAB_MR_VIEW_NOT_APPROVED = (
    '{"iid":54,"state":"opened","source_branch":"kraft/abc",'
    '"merge_status":"cannot_be_merged","detailed_merge_status":"not_approved",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}'
)

GLAB_CI_SUCCESS = (
    '[{"id":2826926699,"iid":141,"status":"success","ref":"main",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/pipelines/2826926699"}]'
)

#: Merged with a merge commit over a squash: the merge commit is what landed.
GLAB_MR_LIST_MERGED = (
    '[{"iid":54,"state":"merged","source_branch":"kraft/abc","sha":"c0ffee3",'
    '"merge_commit_sha":"c0ffee1","squash_commit_sha":"c0ffee2",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}]'
)

#: An open merge request set to merge when its pipeline succeeds.
GLAB_MR_LIST_AUTO_MERGE = (
    '[{"iid":54,"state":"opened","source_branch":"kraft/abc","merge_when_pipeline_succeeds":true,'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}]'
)

#: `head_pipeline` from `glab mr view -F json` for a merged results pipeline,
#: trimmed to what the parser reads (gitlab-org/gitlab !262611, glab 1.123.0,
#: 2026-10-19). It runs on the merge request's merge ref, and its `sha` is a
#: merge commit of the merge request's head into its target, not the head.
GLAB_HEAD_PIPELINE_MERGED_RESULTS = {
    "id": 2962437758,
    "iid": 6607086,
    "project_id": 278964,
    "sha": "92554a945da41e44f0c7cc1db6ff6e599f0e2527",
    "ref": "refs/merge-requests/262611/merge",
    "status": "running",
    "source": "merge_request_event",
    "web_url": "https://gitlab.com/gitlab-org/gitlab/-/pipelines/2962437758",
    "updated_at": "2026-10-19T09:27:00.11Z",
}

#: `glab api projects/278964/repository/commits/<that sha>`, trimmed: the
#: target's head first, then the merge request's head merged into it.
GLAB_MERGE_COMMIT = {
    "id": "92554a945da41e44f0c7cc1db6ff6e599f0e2527",
    "parent_ids": [
        "bcff0c83e340a833b1fe11c39dafa14710d86f83",
        "9352aa50840021714868b50cb2c026b126020ef2",
    ],
}


def glab_merged_results(tested: str, **fields) -> tuple[dict, str, str]:
    """A merged results pipeline (`GLAB_HEAD_PIPELINE_MERGED_RESULTS`, `fields`
    over it) whose merge commit merged `tested` in: the pipeline, the stub
    route that reads its merge commit, and that commit's JSON."""
    pipeline = {**GLAB_HEAD_PIPELINE_MERGED_RESULTS, **fields}
    parents = [GLAB_MERGE_COMMIT["parent_ids"][0], tested]
    commit = json.dumps({"id": pipeline["sha"], "parent_ids": parents})
    return (
        pipeline,
        f"api projects/{pipeline['project_id']}/repository/commits/{pipeline['sha']}",
        commit,
    )


GH_PR_VIEW = (
    '{"number":7,"url":"https://github.com/o/r/pull/7",'
    '"statusCheckRollup":[{"name":"build","conclusion":"SUCCESS"},'
    '{"name":"lint","conclusion":"FAILURE"}]}'
)

GH_PR_VIEW_CONFLICT = (
    '{"number":7,"url":"https://github.com/o/r/pull/7","mergeable":"CONFLICTING",'
    '"mergeStateStatus":"DIRTY","statusCheckRollup":[{"name":"build","conclusion":"SUCCESS"}]}'
)

GH_PR_VIEW_NEEDS_APPROVAL = (
    '{"number":7,"url":"https://github.com/o/r/pull/7","mergeable":"UNKNOWN",'
    '"mergeStateStatus":"BLOCKED","reviewDecision":"REVIEW_REQUIRED",'
    '"statusCheckRollup":[{"name":"build","conclusion":"SUCCESS"}]}'
)

GH_PR_LIST_MERGED = (
    '[{"number":7,"url":"https://github.com/o/r/pull/7","state":"MERGED",'
    '"mergeCommit":{"oid":"c0ffee1"}}]'
)
#: An open pull request with auto-merge enabled: GitHub merges it itself once
#: its checks pass (`gh pr list --json autoMergeRequest`).
GH_PR_LIST_AUTO_MERGE = (
    '[{"number":7,"url":"https://github.com/o/r/pull/7","state":"OPEN",'
    '"autoMergeRequest":{"enabledAt":"2026-09-22T10:00:00Z","mergeMethod":"SQUASH"}}]'
)
