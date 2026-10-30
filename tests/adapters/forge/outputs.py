"""Real `glab`/`gh` output shapes, shared by more than one forge test file.
Captured from glab 1.116.0-1.117.0 and gh 2.100.0 against this repo on
2026-09-07/09; parsers are written against these, not against recollection.
Shapes only one file uses live in that file."""

import json

from .nodes import FAIL

GLAB_MR_VIEW = (
    '{"iid":54,"title":"Draft: Teach it","target_branch":"main","source_branch":"kraft/abc",'
    '"state":"opened",'
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
    '"sha":"f0reign1","web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}]'
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

#: `GET projects/:id/pipelines/<id>` for the pipeline in GLAB_CI_FAILED, trimmed to
#: the fields this code reads (GitLab REST, 2026-10-30; glab 1.117.0 printed
#: this same object with its jobs inline).
GLAB_PIPELINE_FAILED = '{"id":2826926700,"status":"failed"}'

#: `GET projects/:id/pipelines/<id>/jobs` for it: an array, bridge jobs not in it.
GLAB_JOBS_FAILED = (
    '[{"id":16392037101,"name":"lint-and-test","status":"success"},'
    '{"id":16392037104,"name":"release-impact","status":"failed"}]'
)

# The tail of `GET projects/:id/jobs/16392037104/trace`, the only place the reason for MR
# !89's red pipeline was ever written down.
GLAB_CI_TRACE = (
    '$ python3 dev/next_tag.py "" "$CI_MERGE_REQUEST_LABELS" > /dev/null\n'
    "no release:: label; expected one of ('major', 'minor', 'patch', 'none')\n"
    "This MR needs one of: release::major, release::minor, release::patch, release::none\n"
    "ERROR: Job failed: exit code 1\n"
)


def glab_mr(view: str, iid: int | None = None, *, open: bool = True) -> dict:
    """Stub routes for `GlabCli._mr`: the open-MR list for the branch, else (not
    `open`) the empty one and then the all-states list, then the merge request
    itself by its iid. `FAIL` makes the first list call fail."""
    if view is FAIL:
        return {"api*state=opened*": FAIL}
    iid = iid or json.loads(view)["iid"]
    listed = json.dumps([{"iid": iid}])
    lists = (
        {"api*state=opened*": listed}
        if open
        else {"api*state=opened*": "[]", "api*state=all -F per_page=1": listed}
    )
    return {**lists, f"api projects/:id/merge_requests/{iid}": view}


PIPELINES = "api projects/:id/pipelines"


def glab_pipeline(
    pipeline: str = GLAB_PIPELINE_FAILED, jobs: str = GLAB_JOBS_FAILED, trace: str = GLAB_CI_TRACE
) -> dict:
    """Stub routes for reading one pipeline by id: the pipeline, its jobs, and
    each job's trace. `FAIL` as the pipeline or the jobs fails that call."""
    pid = json.loads(pipeline)["id"] if pipeline is not FAIL else 2826926700
    routes = {f"{PIPELINES}/{pid}": pipeline}
    if pipeline is FAIL:
        return routes
    routes[f"{PIPELINES}/{pid}/jobs"] = jobs
    if jobs is FAIL:
        return routes
    return routes | {f"api projects/:id/jobs/{j['id']}/trace": trace for j in json.loads(jobs)}
