# Intent: waiting for a pipeline by id

A forge task, `ci.pipeline`, waits for one CI pipeline named by its id: a GitLab
pipeline `id` or a GitHub Actions run id, given as digits or as `$NAME` from a
step output. It reads and never writes: the id may come from a worker, so it
retries no job, and a pipeline it will not judge stops the item for a person.

## REQ ci-pipeline-takes-a-pipeline-id

IF a `ci.pipeline` task has no `pipeline`, or one that is neither digits nor `$NAME`, THEN the
system SHALL refuse the chain, with a message that does not contain the value.
enforced-by: tests/templates/test_ci_pipeline_task.py::test_ci_pipeline_refusals[missing], tests/templates/test_ci_pipeline_task.py::test_ci_pipeline_refusals[letters], tests/templates/test_ci_pipeline_task.py::test_ci_pipeline_refusals[trailing-newline], tests/templates/test_ci_pipeline_task.py::test_a_bad_value_is_never_echoed
origin: src/kraft/templates/models.py §ForgeTask

## REQ ci-pipeline-is-for-ci-pipeline-alone

IF a forge target other than `ci.pipeline` has a `pipeline`, THEN the system SHALL refuse the chain.
enforced-by: tests/templates/test_ci_pipeline_task.py::test_ci_pipeline_refusals[other-target]
origin: src/kraft/templates/models.py §ForgeTask

## REQ ci-pipeline-waits-for-one-pipeline

IF a `ci.pipeline` task runs once per repository, THEN the system SHALL refuse the chain.
enforced-by: tests/templates/test_ci_pipeline_task.py::test_ci_pipeline_refusals[fan-out]
origin: src/kraft/templates/models.py §ForgeTask

## REQ ci-pipeline-id-comes-from-a-declared-variable

IF a `ci.pipeline` task's `pipeline` is `$NAME` and no task in its `reads` declares `NAME`, THEN the
system SHALL refuse the chain.
enforced-by: tests/templates/test_ci_pipeline_task.py::test_ci_pipeline_variable_refusals[no-writer-declares-it], tests/templates/test_ci_pipeline_task.py::test_ci_pipeline_variable_refusals[no-reads], tests/templates/test_ci_pipeline_task.py::test_ci_pipeline_variable_refusals[unclosed-brace], tests/templates/test_ci_pipeline_task.py::test_ci_pipeline_variable_refusals[stray-brace], tests/templates/test_ci_pipeline_task.py::test_ci_pipeline_variable_refusals[text-around-it], tests/templates/test_ci_pipeline_task.py::test_ci_pipeline_reads_its_id_from_a_writers_variable[$PIPELINE_ID], tests/templates/test_ci_pipeline_task.py::test_ci_pipeline_reads_its_id_from_a_writers_variable[${PIPELINE_ID}]
origin: src/kraft/templates/models.py §Chain

## REQ ci-pipeline-resolves-its-variable-at-every-look

WHEN a `ci.pipeline` task looks at its pipeline, the system SHALL read `$NAME` afresh from the
snapshots of the writers in its `reads`, a later writer winning.
enforced-by: tests/adapters/forge/test_run_pipeline.py::test_pipeline_from_variable[set], tests/adapters/forge/test_run_pipeline.py::test_pipeline_from_variable[later-writer-wins], tests/adapters/forge/test_run_pipeline.py::test_a_walk_hands_the_writers_variable_to_the_wait
origin: src/kraft/adapters/forge/run.py §_pipeline_wait

## REQ ci-pipeline-refuses-an-unusable-id-without-quoting-it

IF the pipeline id is unset, not digits, or unknown to the forge, THEN the system SHALL stop the
item for a person with a message that does not contain the id, and settle the wait.
enforced-by: tests/adapters/forge/test_run_pipeline.py::test_pipeline_from_variable[unset], tests/adapters/forge/test_run_pipeline.py::test_pipeline_from_variable[undeclared-in-current-chain], tests/adapters/forge/test_run_pipeline.py::test_pipeline_from_variable[value-not-digits], tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait_not_found_tells_which_id_to_give_and_never_quotes_it, tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait_bad_value_closes_waiting_row[letters], tests/adapters/forge/test_run_pipeline.py::test_a_variable_that_goes_unset_closes_the_parked_wait
origin: src/kraft/adapters/forge/run.py §_pipeline_wait

## REQ ci-pipeline-follows-a-re-resolved-id

WHEN a `ci.pipeline` task resolves a different id than its open wait was started about, the
system SHALL end that wait as superseded and start a new one.
enforced-by: tests/test_waits_observe.py::test_a_wait_about_another_pipeline_supersedes_the_open_one, tests/test_waits_observe.py::test_a_look_about_the_same_pipeline_keeps_its_wait
origin: src/kraft/waits.py §observe

## REQ ci-pipeline-never-writes-to-the-forge

The system SHALL NOT retry a job, push, look for a merge request or record a pipeline reference
while a `ci.pipeline` task looks at its pipeline.
enforced-by: tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait_never_writes, tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait[infra-red]
origin: src/kraft/adapters/forge/run.py §_pipeline_wait

## REQ ci-pipeline-ignores-ci-checks

WHERE a repository sets `ci_checks: false`, the system SHALL still read the pipeline a `ci.pipeline`
task names.
enforced-by: tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait_ignores_ci_checks_false
origin: src/kraft/adapters/forge/run.py §_pipeline_wait

## REQ ci-pipeline-green-is-the-pipelines-own-success

WHEN the forge reports a pipeline successful and not empty, the system SHALL finish the task.
enforced-by: tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait[success], tests/adapters/forge/test_glab_reads.py::test_pipeline_status_glab[success], tests/adapters/forge/test_glab_reads.py::test_pipeline_status_glab[success-with-allow-failure-job], tests/adapters/forge/test_gh_reads.py::test_pipeline_status_gh[success]
origin: src/kraft/adapters/forge/glab.py §pipeline_status

## REQ ci-pipeline-waits-on-a-person-and-says-so

WHEN a pipeline is running, or waits on a manual job or an approval, the system SHALL keep waiting
and name what it waits on.
enforced-by: tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait[running], tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait_blocked_on_a_manual_job_waits_and_says_which, tests/adapters/forge/test_glab_reads.py::test_pipeline_status_glab[manual], tests/adapters/forge/test_gh_reads.py::test_pipeline_status_gh[waiting]
origin: src/kraft/adapters/forge/glab.py §pipeline_status

## REQ ci-pipeline-red-fails-the-task-with-findings

WHEN a pipeline finishes with failed jobs, the system SHALL fail the task with one finding per
failed job.
enforced-by: tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait[red], tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait_red_writes_one_finding_per_failed_job, tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait_workspace_keeps_findings
origin: src/kraft/adapters/forge/run.py §_pipeline_wait

## REQ ci-pipeline-stops-for-a-person-on-what-it-will-not-judge

WHEN a pipeline failed on infrastructure, ran nothing, or was cancelled with no successor, the
system SHALL stop the item for a person, naming the cause first.
enforced-by: tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait[infra-red], tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait[nothing-ran], tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait[cancelled-after-grace]
origin: src/kraft/adapters/forge/run.py §_pipeline_wait

## REQ ci-pipeline-times-out-for-a-person

WHEN a pipeline is still pending at the wait's deadline, the system SHALL stop the item as a
timed-out wait.
enforced-by: tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait_that_never_settles_times_out_for_a_person
origin: src/kraft/adapters/forge/run.py §_observed

## REQ ci-pipeline-keeps-waiting-through-an-outage

WHEN the forge cannot be reached while a `ci.pipeline` task looks, the system SHALL keep waiting.
enforced-by: tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait_keeps_waiting_through_an_outage_then_settles[cli-outage], tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait_keeps_waiting_through_an_outage_then_settles[transport-unreachable]
origin: src/kraft/adapters/forge/run.py §_pipeline_wait

## REQ ci-pipeline-lets-an-auth-or-rate-limit-error-stop-as-usual

IF the forge refuses the token or answers a rate limit, THEN the system SHALL stop as any forge
task does.
enforced-by: tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait_lets_other_forge_errors_take_their_usual_stop[auth-error], tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait_lets_other_forge_errors_take_their_usual_stop[rate-limited], tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait_auth_failure_keeps_the_forge_cause
origin: src/kraft/adapters/forge/run.py §run_task

## REQ ci-pipeline-reads-the-root-of-a-workspace

WHERE an item is a workspace, the system SHALL read a `ci.pipeline` task's pipeline in the root
repository once, whichever members changed.
enforced-by: tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait_workspace_reads_root[targets], tests/adapters/forge/test_run_pipeline.py::test_pipeline_wait_workspace_reads_root[no-targets]
origin: src/kraft/adapters/forge/run.py §run_task

## REQ ci-pipeline-reads-a-github-run-by-its-latest-attempt-in-this-repository

WHEN a GitHub run is read by id, the system SHALL report its latest attempt.
enforced-by: tests/adapters/forge/test_gh_reads.py::test_pipeline_status_gh_follows_the_latest_attempt_and_names_it
origin: src/kraft/adapters/forge/gh.py §pipeline_status

## REQ ci-pipeline-refuses-a-forks-run

IF a GitHub run read by id is a fork's, or the forge has none by that id, THEN the system SHALL
report it as not found.
enforced-by: tests/adapters/forge/test_gh_reads.py::test_pipeline_status_gh_refuses_a_forks_run_as_not_found, tests/adapters/forge/test_gh_reads.py::test_pipeline_status_gh_404_is_not_found
origin: src/kraft/adapters/forge/gh.py §pipeline_status

## REQ a-failed-gitlab-bridge-is-code-red

WHEN a GitLab pipeline's downstream bridge failed, the system SHALL read it as a failure of the
code, not of the infrastructure.
enforced-by: tests/adapters/forge/test_glab.py::test_glab_ci_status_reads_the_failed_jobs[failed-bridge], tests/adapters/forge/test_glab.py::test_glab_ci_status_reads_the_failed_jobs[bridges-unreadable-no-jobs], tests/adapters/forge/test_glab.py::test_glab_ci_status_reads_the_failed_jobs[green-bridge-failed-job], tests/adapters/forge/test_glab.py::test_glab_ci_status_reads_the_failed_jobs[failed-job-and-failed-bridge]
origin: src/kraft/adapters/forge/glab.py §_failure_detail

## REQ a-red-ci-pipeline-is-not-stuck

IF a `ci.pipeline` task fails, THEN the system SHALL stop the item for a person without running
the node's `escalation` or `auto_escalate_stuck`.
enforced-by: tests/executor/test_stuck_escalation.py::test_red_pipeline_is_not_stuck[no-on-failure], tests/executor/test_stuck_escalation.py::test_red_pipeline_is_not_stuck[fix-loop-cap-spent], tests/executor/test_stuck_escalation.py::test_red_pipeline_is_not_stuck[auth-failure], tests/executor/test_stuck_escalation.py::test_every_stuck_stop_on_a_red_pipeline_goes_to_a_person[doubted-repair], tests/executor/test_stuck_escalation.py::test_every_stuck_stop_on_a_red_pipeline_goes_to_a_person[judge-stop], tests/executor/test_stuck_escalation.py::test_every_stuck_stop_on_a_red_pipeline_goes_to_a_person[repeat-fingerprint], tests/executor/test_stuck_escalation.py::test_every_stuck_stop_on_a_red_pipeline_goes_to_a_person[fix-changed-nothing], tests/executor/test_stuck_escalation.py::test_red_pipeline_runs_on_failure
origin: src/kraft/executor/walk.py §_red_pipeline_only

## REQ ci-log-text-is-framed-as-data-for-a-fix-agent

WHEN a fix agent is handed a finding that is CI log text, the system SHALL say it is data, not
instructions.
enforced-by: tests/executor/test_prompts.py::test_format_findings_frames_ci_log_text_as_data
origin: src/kraft/executor/round_prompts.py §format_findings

## REQ a-ci-pipeline-node-is-not-a-merge-request-node

A node whose only forge tasks are `ci.pipeline` SHALL NOT stop the item when its merge request is
closed.
enforced-by: tests/test_mr_poller.py::test_tick_skips_a_non_mr_node_without_calling_find_mr[ci-pipeline-only-node-is-not-mr]
origin: src/kraft/mr_poller.py §_is_mr_node

## REQ a-ci-pipeline-node-may-be-skipped-by-a-revision

A chain revision SHALL be allowed to skip a node whose only forge tasks are `ci.pipeline`, and IF
it skips a writer whose reader stays, THEN the system SHALL refuse it, naming the reader's `reads`.
enforced-by: tests/templates/test_revision.py::test_a_revision_may_skip_a_ci_pipeline_node_and_its_writer, tests/templates/test_revision.py::test_skipping_a_writer_whose_reader_stays_names_the_reads_it_breaks
origin: src/kraft/templates/revision.py §_lands

## REQ a-ci-pipeline-does-not-publish-the-branch

A gate after a `ci.pipeline` node and before any other forge task SHALL count as before any push.
enforced-by: tests/api/test_gates_refresh.py::test_a_gate_after_a_pipeline_wait_is_still_before_any_push[ci.pipeline-True], tests/api/test_gates_refresh.py::test_a_gate_after_a_pipeline_wait_is_still_before_any_push[mr.sync-False]
origin: src/kraft/api/routes/gates.py §_before_any_push

## REQ ci-pipeline-is-not-offered-by-kraft-lite

IF a Kraft Lite chain uses `ci.pipeline`, THEN the system SHALL refuse it by name.
enforced-by: plugins/kraft-lite/tests/test_chains.py::test_chain_extension_refusals[ci_pipeline]
origin: plugins/kraft-lite/kl_chains.py
