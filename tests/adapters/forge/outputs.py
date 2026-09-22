"""Real `glab`/`gh` output shapes, shared by more than one forge test file.
Captured from glab 1.116.0-1.117.0 and gh 2.100.0 against this repo on
2026-09-07/09; parsers are written against these, not against recollection.
Shapes only one file uses live in that file."""

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

GLAB_MR_LIST_MERGED = (
    '[{"iid":54,"state":"merged","source_branch":"kraft/abc",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}]'
)

#: An open merge request set to merge when its pipeline succeeds.
GLAB_MR_LIST_AUTO_MERGE = (
    '[{"iid":54,"state":"opened","source_branch":"kraft/abc","merge_when_pipeline_succeeds":true,'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}]'
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

GH_PR_LIST_MERGED = '[{"number":7,"url":"https://github.com/o/r/pull/7","state":"MERGED"}]'
#: An open pull request with auto-merge enabled: GitHub merges it itself once
#: its checks pass (`gh pr list --json autoMergeRequest`).
GH_PR_LIST_AUTO_MERGE = (
    '[{"number":7,"url":"https://github.com/o/r/pull/7","state":"OPEN",'
    '"autoMergeRequest":{"enabledAt":"2026-09-22T10:00:00Z","mergeMethod":"SQUASH"}}]'
)
