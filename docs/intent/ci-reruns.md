# Intent: a repair re-runs CI that failed outside the code

A fix-loop repair on a node that waits for the merge request's CI can find a
failed job died before any check of the code ran (a download 504, a runner that
went away). It asks Kraft to re-run the failed jobs instead of changing code. The
fix loop counts that as a change, so the item goes on measuring within the loop's
own attempts instead of stopping stuck, and a stop after re-runs says the failure
was infrastructure. Kraft's own forge-flagged self-retry is a separate budget.

## REQ a-rerun-records-itself

WHEN a repair asks to re-run the failed CI jobs of a red merge request, the system SHALL re-run
them and record a `ci_rerun_requested` event naming the node, the cycle, the reason and the jobs.
enforced-by: tests/api/test_ci_rerun.py::test_a_rerun_re_runs_the_failed_jobs_and_records_it
origin: src/kraft/api/routes/lifecycle.py §ci_rerun

## REQ a-rerun-needs-a-red-open-fix-attempt

IF no fix attempt is open on a node that waits on merge-request CI, or CI is not red for the
branch's current head, or nothing in it can be re-run, THEN the system SHALL refuse the re-run and
record no event.
enforced-by: tests/api/test_ci_rerun.py::test_a_rerun_is_refused[node-without-mr-ci], tests/api/test_ci_rerun.py::test_a_rerun_is_refused[no-open-cycle], tests/api/test_ci_rerun.py::test_a_rerun_is_refused[ci-green], tests/api/test_ci_rerun.py::test_a_rerun_is_refused[ci-pending], tests/api/test_ci_rerun.py::test_a_rerun_is_refused[stale-head], tests/api/test_ci_rerun.py::test_a_rerun_is_refused[nothing-re-runnable]
origin: src/kraft/api/routes/lifecycle.py §ci_rerun

## REQ a-fix-attempt-re-runs-ci-once

IF CI was already re-run in the open fix attempt, THEN the system SHALL refuse a second re-run, and
WHEN the next attempt opens it SHALL allow one again.
enforced-by: tests/api/test_ci_rerun.py::test_a_rerun_is_refused[already-this-attempt], tests/api/test_ci_rerun.py::test_a_rerun_in_the_next_attempt_is_allowed
origin: src/kraft/ci_reruns.py §open_fix_cycle

## REQ a-ci-rerun-round-is-a-change

WHEN a fix round's only act is a CI re-run, the system SHALL measure again, and SHALL NOT stop the
item as unchanged because the re-run came back with the same findings.
enforced-by: tests/executor/test_fix_loop_outcomes.py::test_a_fix_round_that_changed_anything_measures_again[ci-rerun], tests/executor/test_stuck_escalation.py::test_a_ci_rerun_round_with_the_same_findings_runs_to_the_cap
origin: src/kraft/executor/walk.py §_changed_nothing

## REQ a-stop-after-ci-reruns-says-infrastructure

WHEN a fix loop whose attempts re-ran CI stops, the system SHALL name the re-runs and say the
failure was infrastructure in the stop reason, and SHALL tell the judge the round re-ran CI.
enforced-by: tests/executor/test_stuck_escalation.py::test_a_ci_rerun_round_with_the_same_findings_runs_to_the_cap, tests/executor/test_stuck_escalation.py::test_a_judge_after_a_ci_rerun_is_told_and_its_stop_says_so, tests/executor/test_fix_loop_outcomes.py::test_a_fix_round_that_changed_nothing_stops_without_measuring_again[after-an-earlier-ci-rerun]
origin: src/kraft/executor/prompts.py §ci_rerun_stop_note

## REQ a-sandboxed-repair-re-runs-only-its-own-item

WHERE a sandboxed worker has a `network:` channel, the system SHALL let it re-run CI for its own
item only.
enforced-by: tests/worker/test_callback.py::test_an_item_route_is_allowed_for_the_session_own_item_only[/api/work-items/{}/ci-rerun], tests/worker/test_egress_worker_api.py::test_another_item_is_refused_and_recorded[another-item-rerun-ci]
origin: src/kraft/worker/callback.py §ROUTES
