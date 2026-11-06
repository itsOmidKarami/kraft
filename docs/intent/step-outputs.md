# Intent: step outputs

What a task leaves behind for a later task, and how Kraft keeps it. A task
declares `outputs` and writes into `$KRAFT_OUTPUT_DIR`; a later task names it in
`reads` and gets a checked, read-only snapshot under `$KRAFT_READS_DIR`. The
worker is untrusted: nothing it writes reaches an environment, a message or a
prompt as anything but data, and no read of its folder follows a link.

## REQ an-outputs-declaration-declares-something

IF a task's `outputs` names neither a file nor a variable, THEN the system SHALL refuse the chain.
enforced-by: tests/templates/test_step_outputs.py::test_outputs_decl_refuses[empty], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[both-lists-empty]
origin: src/kraft/templates/models.py §OutputsDecl

## REQ an-output-file-is-a-plain-relative-name

IF a declared output file is empty, absolute, climbs out of the output folder, has an empty or `.` segment, a backslash, a control character or a glob, is `variables.env` in any case, differs from another only by case, or is a folder of another, THEN the system SHALL refuse the chain.
enforced-by: tests/templates/test_step_outputs.py::test_outputs_decl_refuses[parent], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[absolute], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[empty-name], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[glob], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[variables-file], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[case-twins], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[dot], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[empty-segment], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[backslash], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[control-character], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[variables-file-by-another-case], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[file-and-its-own-folder], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[folder-and-a-file-by-another-case], tests/templates/test_step_outputs.py::test_outputs_decl_accepts[plain], tests/templates/test_step_outputs.py::test_outputs_decl_accepts[space]
origin: src/kraft/templates/models.py §OutputsDecl

## REQ an-output-variable-is-upper-snake-case

IF a declared output variable does not match `[A-Z][A-Z0-9_]*` as a whole, or is declared twice, THEN the system SHALL refuse the chain.
enforced-by: tests/templates/test_step_outputs.py::test_outputs_decl_refuses[lowercase-variable], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[digit-first-variable], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[variable-with-trailing-newline], tests/templates/test_step_outputs.py::test_outputs_decl_refuses[repeated-variable], tests/templates/test_step_outputs.py::test_outputs_decl_accepts[variable]
origin: src/kraft/templates/models.py §OutputsDecl

## REQ outputs-are-refused-where-the-task-runs-more-than-once-or-under-a-handler

IF a fan-out task, a recovery, conflict or merge-rejection handler, a fix loop, a judge, an escalation, a gate's reviewer or a non-agent, non-subprocess task declares `outputs`, THEN the system SHALL refuse the chain.
enforced-by: tests/templates/test_step_outputs.py::test_outputs_refused_where_not_allowed[fan-out], tests/templates/test_step_outputs.py::test_outputs_refused_where_not_allowed[task-on-failure], tests/templates/test_step_outputs.py::test_outputs_refused_where_not_allowed[step-on-failure], tests/templates/test_step_outputs.py::test_outputs_refused_where_not_allowed[node-on-failure], tests/templates/test_step_outputs.py::test_outputs_refused_where_not_allowed[fix-loop-repair], tests/templates/test_step_outputs.py::test_outputs_refused_where_not_allowed[judge], tests/templates/test_step_outputs.py::test_outputs_refused_where_not_allowed[escalation], tests/templates/test_step_outputs.py::test_outputs_refused_where_not_allowed[on-conflict], tests/templates/test_step_outputs.py::test_outputs_refused_where_not_allowed[merge-rejected-repair], tests/templates/test_step_outputs.py::test_outputs_refused_where_not_allowed[auto-review], tests/templates/test_step_outputs.py::test_outputs_refused_where_not_allowed[forge-task]
origin: src/kraft/templates/models.py §_refuse_outputs

## REQ reads-are-refused-on-a-task-that-takes-none

IF a builtin task or a forge task declares `reads`, THEN the system SHALL refuse the chain.
enforced-by: tests/templates/test_step_outputs.py::test_reads_refused_on[builtin], tests/templates/test_step_outputs.py::test_reads_refused_on[mr.open_draft], tests/templates/test_step_outputs.py::test_reads_refused_on[mr.sync], tests/templates/test_step_outputs.py::test_reads_refused_on[mr.ci], tests/templates/test_step_outputs.py::test_reads_refused_on[mr.automated_review], tests/templates/test_step_outputs.py::test_reads_refused_on[mr.mark_ready], tests/templates/test_step_outputs.py::test_reads_refused_on[mr.external_approval], tests/templates/test_step_outputs.py::test_reads_refused_on[mr.merge], tests/templates/test_step_outputs.py::test_reads_refused_on[mr.post_merge_ci]
origin: src/kraft/templates/models.py §TaskBase

## REQ a-read-is-not-a-bare-task-id

IF a `reads` entry has fewer than two segments, more than three or an empty one, THEN the system SHALL refuse the chain.
enforced-by: tests/templates/test_step_outputs.py::test_resolve_reads[bare-id], tests/templates/test_step_outputs.py::test_resolve_reads[empty-segment], tests/templates/test_step_outputs.py::test_resolve_reads[empty], tests/templates/test_step_outputs.py::test_resolve_reads[four-segments], tests/templates/test_step_outputs.py::test_reads_must_be_qualified_on_the_kinds_that_take_it[subprocess], tests/templates/test_step_outputs.py::test_reads_must_be_qualified_on_the_kinds_that_take_it[agent]
origin: src/kraft/templates/models.py §resolve_reads

## REQ a-two-segment-read-names-a-writer-in-the-readers-own-node

WHEN a `reads` entry has two segments, the system SHALL read it as a task of the reader's own node, and with three as the path written.
enforced-by: tests/templates/test_step_outputs.py::test_resolve_reads[own-node], tests/templates/test_step_outputs.py::test_resolve_reads[other-node], tests/templates/test_step_outputs.py::test_resolve_reads[both], tests/templates/test_step_outputs.py::test_a_library_node_used_under_two_ids_reads_by_step_and_task
origin: src/kraft/templates/models.py §resolve_reads

## REQ a-read-names-an-earlier-task-that-declares-outputs

IF a `reads` entry names a task that is missing, declares no `outputs`, or is not in an earlier node or an earlier step of the reader's node, THEN the system SHALL refuse the chain.
enforced-by: tests/templates/test_step_outputs.py::test_reads_ordering_refuses[missing-writer], tests/templates/test_step_outputs.py::test_reads_ordering_refuses[writer-declares-nothing], tests/templates/test_step_outputs.py::test_reads_ordering_refuses[later-node], tests/templates/test_step_outputs.py::test_reads_ordering_refuses[later-step], tests/templates/test_step_outputs.py::test_reads_ordering_refuses[same-step-sibling], tests/templates/test_step_outputs.py::test_reads_ordering_refuses[fan-out-task], tests/templates/test_step_outputs.py::test_reads_ordering_refuses[handler-slot-task], tests/templates/test_step_outputs.py::test_reads_ordering_accepts[earlier-node], tests/templates/test_step_outputs.py::test_reads_ordering_accepts[earlier-step]
origin: src/kraft/templates/models.py §_reads_name_earlier_writers

## REQ a-handler-reads-the-steps-before-where-it-runs

WHEN a handler of a node reads, the system SHALL allow every step for a node's `on_failure`, fix loop, judge and escalation, and the steps up to its own for a step's or task's `on_failure`.
enforced-by: tests/templates/test_step_outputs.py::test_reads_ordering_refuses[step-on-failure-of-earlier-step], tests/templates/test_step_outputs.py::test_reads_ordering_accepts[node-on-failure], tests/templates/test_step_outputs.py::test_reads_ordering_accepts[fix-loop-repair], tests/templates/test_step_outputs.py::test_reads_ordering_accepts[judge], tests/templates/test_step_outputs.py::test_reads_ordering_accepts[escalation], tests/templates/test_step_outputs.py::test_reads_ordering_accepts[step-on-failure-of-its-own-step], tests/templates/test_step_outputs.py::test_reads_ordering_accepts[step-on-failure-of-an-earlier-writer], tests/templates/test_step_outputs.py::test_reads_ordering_accepts[task-on-failure]
origin: src/kraft/templates/models.py §_reads_name_earlier_writers

## REQ a-conflict-handler-a-repair-and-a-gate-review-read-nothing

IF a conflict handler, a merge-rejection repair or a gate's reviewer declares `reads`, THEN the system SHALL refuse the chain.
enforced-by: tests/templates/test_step_outputs.py::test_reads_ordering_refuses[on-conflict], tests/templates/test_step_outputs.py::test_reads_ordering_refuses[merge-rejected-repair], tests/templates/test_step_outputs.py::test_reads_ordering_refuses[auto-review]
origin: src/kraft/templates/models.py §_reads_name_earlier_writers

## REQ a-stored-chain-with-no-outputs-or-reads-loads-again

The system SHALL load a stored chain that carries the default `reads: []` and `outputs: null` on every task kind.
enforced-by: tests/templates/test_step_outputs.py::test_dumped_defaults_round_trip
origin: src/kraft/templates/models.py §MaterializedChain

## REQ the-chain-editor-writes-outputs-and-reads-as-lists

WHEN the chain editor sets `outputs.files`, `outputs.variables` or `reads`, the system SHALL write each as a list of names.
enforced-by: tests/drafts/test_ops_step_outputs.py::test_set_field_writes_outputs_and_reads_as_lists, frontend/src/ng/templates/fields.test.ts::the step output keys a Config row edits
origin: src/kraft/drafts/authored.py §NESTED_KEYS

## REQ kraft-lite-refuses-outputs-and-reads

IF a Kraft Lite chain's task declares `outputs` or `reads`, THEN the system SHALL refuse it by name.
enforced-by: plugins/kraft-lite/tests/test_chains.py::test_chain_extension_refusals[task_outputs], plugins/kraft-lite/tests/test_chains.py::test_chain_extension_refusals[task_reads]
origin: plugins/kraft-lite/kl_chains.py

## REQ a-writers-output-folder-is-in-its-sessions-own-out

The system SHALL give a writer `$KRAFT_OUTPUT_DIR` as `out/outputs/` in its own session's folder, whatever folder its result file is in.
enforced-by: tests/test_outputs.py::test_writer_dir_ignores_writable, tests/executor/test_dispatch_outputs.py::test_writer_launch[agent], tests/executor/test_dispatch_outputs.py::test_writer_launch[subprocess]
origin: src/kraft/item_paths.py §writer_dir

## REQ a-writer-is-told-its-folder-and-the-names-to-keep

WHEN the system launches an agent writer, it SHALL append a note naming `$KRAFT_OUTPUT_DIR` and the declared names to its prompt.
enforced-by: tests/executor/test_dispatch_outputs.py::test_writer_launch[agent]
origin: src/kraft/executor/prompts.py §outputs_note

## REQ each-fallback-candidate-gets-its-own-output-folder

WHEN a launch falls back to another candidate, the system SHALL give that candidate's session its own output folder.
enforced-by: tests/executor/test_dispatch_outputs.py::test_each_fallback_candidate_gets_its_own_output_dir
origin: src/kraft/executor/dispatch.py §_launch_agent

## REQ a-resumed-writer-starts-with-the-paused-sessions-outputs

WHEN a paused agent resumes under a new session, the system SHALL copy the paused session's output folder into the new one on a thread, following no link and holding the whole copy to the size cap, and keep the note in its prompt.
enforced-by: tests/executor/test_dispatch_outputs.py::test_resumed_agent_keeps_outputs, tests/executor/test_dispatch_outputs.py::test_outputs_note_survives_resume, tests/executor/test_dispatch_outputs.py::test_the_output_folder_is_made_and_carried_off_the_event_loop[agent], tests/executor/test_dispatch_outputs.py::test_the_output_folder_is_made_and_carried_off_the_event_loop[subprocess], tests/test_outputs.py::test_carry_over_holds_the_whole_folder_to_the_cap
origin: src/kraft/outputs.py §carry_over

## REQ a-reader-gets-only-its-writers-snapshots

WHEN the system launches a task with `reads`, it SHALL set `KRAFT_READS_DIR` and mount read-only only the snapshot folders of the writers it names that have one.
enforced-by: tests/executor/test_dispatch_outputs.py::test_reader_launch[agent], tests/executor/test_dispatch_outputs.py::test_reader_launch[subprocess], tests/executor/test_dispatch_outputs.py::test_reader_without_reads_gets_no_env, tests/executor/test_dispatch_outputs.py::test_reader_gets_nothing_from_missing_snapshot, tests/executor/test_dispatch_outputs.py::test_each_fallback_candidate_gets_the_reads
origin: src/kraft/executor/dispatch.py §_reads_mounts

## REQ a-readers-prompt-says-the-files-are-data

WHEN the system launches an agent reader, it SHALL list the writers' paths and declared variable names in its prompt, and say the files are data written by another task, not instructions.
enforced-by: tests/executor/test_dispatch_outputs.py::test_reads_note_survives_instruction_override[on_failure], tests/executor/test_dispatch_outputs.py::test_reads_note_survives_instruction_override[repair], tests/executor/test_dispatch_outputs.py::test_reads_note_survives_instruction_override[judge], tests/executor/test_dispatch_outputs.py::test_reads_note_survives_instruction_override[escalation], tests/executor/test_prompts.py::test_reads_note_lists_paths_and_variable_names_and_says_it_is_data, tests/executor/test_dispatch_outputs.py::test_declared_variables_are_the_chains_current_declaration
origin: src/kraft/executor/prompts.py §reads_note

## REQ a-new-run-of-a-writer-clears-its-snapshot

WHEN the system dispatches a writer for a new run, it SHALL delete the writer's snapshot, and keep it for a session it reuses.
enforced-by: tests/executor/test_dispatch_outputs.py::test_redispatch_clears_snapshot[new-run], tests/executor/test_dispatch_outputs.py::test_redispatch_clears_snapshot[reused-session-keeps], tests/executor/test_dispatch_outputs.py::test_writer_rerun_after_reader_passed_does_not_retrigger
origin: src/kraft/executor/dispatch.py §measure_node

## REQ a-skip-clears-the-snapshots-of-the-writers-it-skips

WHEN a task, a step or a node is skipped, the system SHALL delete the snapshots of the writers under it, a running writer's included.
enforced-by: tests/api/test_skip_outputs.py::test_skip_clears_snapshot[task-before-run], tests/api/test_skip_outputs.py::test_skip_clears_snapshot[task-while-running], tests/api/test_skip_outputs.py::test_skip_clears_snapshot[step], tests/api/test_skip_outputs.py::test_skip_clears_snapshot[node]
origin: src/kraft/api/routes/lifecycle.py §_clear_outputs

## REQ a-writer-that-ran-to-its-end-has-its-outputs-captured

WHEN a session that declared outputs ends done or failed, the system SHALL check its output folder and keep the declared names in a snapshot.
enforced-by: tests/adapters/test_subprocess_outputs.py::test_capture_on_exit[done], tests/adapters/test_subprocess_outputs.py::test_capture_on_exit[failed], tests/test_outputs.py::test_capture_keeps[plain], tests/test_outputs.py::test_capture_keeps[space-in-name], tests/test_outputs.py::test_capture_keeps[non-ascii], tests/test_outputs.py::test_capture_keeps[crlf]
origin: src/kraft/adapters/subprocess.py §capture_outputs

## REQ a-session-that-did-not-run-to-its-end-is-not-captured

WHEN a session ends paused, capped out, cancelled or unknown, the system SHALL leave its output folder untouched.
enforced-by: tests/adapters/test_subprocess_outputs.py::test_a_status_that_is_not_a_finished_run_keeps_its_folder[unknown], tests/adapters/test_subprocess_outputs.py::test_a_status_that_is_not_a_finished_run_keeps_its_folder[paused], tests/adapters/test_subprocess_outputs.py::test_a_status_that_is_not_a_finished_run_keeps_its_folder[needs_context], tests/adapters/test_subprocess_outputs.py::test_a_status_that_is_not_a_finished_run_keeps_its_folder[rate_limited], tests/test_outputs.py::test_capture_without_sidecar_is_none
origin: src/kraft/adapters/subprocess.py §capture_outputs

## REQ a-capture-checks-everything-before-it-copies

IF the output folder is over the size cap or holds too many entries, a declared variable is missing, or a value is too long or holds a control character, THEN the system SHALL keep nothing and name no string the worker chose.
enforced-by: tests/test_outputs.py::test_capture_fails[total-size-over-the-cap], tests/test_outputs.py::test_capture_fails[101-entries], tests/test_outputs.py::test_capture_fails[declared-variable-missing], tests/test_outputs.py::test_capture_fails[value-of-4097-bytes], tests/test_outputs.py::test_capture_fails[escape-in-a-value], tests/test_outputs.py::test_capture_fails[nul-in-a-value], tests/test_outputs.py::test_capture_fails[51-variables], tests/test_outputs.py::test_capture_fails[files-fine-but-a-variable-fails]
origin: src/kraft/outputs.py §capture

## REQ a-capture-leaves-out-what-is-not-a-plain-file

The system SHALL leave a symlink, a file with another hard link and a FIFO out of a snapshot, and count it missing.
enforced-by: tests/test_outputs.py::test_capture_skips_unsafe_files[symlink], tests/test_outputs.py::test_capture_skips_unsafe_files[hardlink], tests/test_outputs.py::test_capture_skips_unsafe_files[fifo]
origin: src/kraft/outputs.py §capture

## REQ variables-env-is-read-as-key-value-lines

The system SHALL read `variables.env` as `KEY=value` lines, skipping blank and `#` lines, dropping one trailing carriage return, keeping an `=` in a value and the last of a repeated key.
enforced-by: tests/test_outputs.py::test_variables_env_parsing[comments-and-blanks], tests/test_outputs.py::test_variables_env_parsing[equals-in-value], tests/test_outputs.py::test_variables_env_parsing[last-wins], tests/test_outputs.py::test_variables_env_parsing[no-quote-stripping], tests/test_outputs.py::test_variables_env_parsing[crlf], tests/test_outputs.py::test_variables_env_parsing[no-equals]
origin: src/kraft/outputs.py §parse_variables

## REQ a-capture-replaces-the-previous-snapshot-and-deletes-the-writers-folder

WHEN a capture ends, the system SHALL replace the writer's previous snapshot with the new one, or with none when the capture failed, and delete the writer's output folder.
enforced-by: tests/test_outputs.py::test_capture_replaces_previous_snapshot, tests/test_outputs.py::test_a_failed_capture_leaves_no_snapshot_of_an_earlier_run[check-fails], tests/test_outputs.py::test_a_failed_capture_leaves_no_snapshot_of_an_earlier_run[walk-raises], tests/test_outputs.py::test_a_failed_capture_leaves_no_snapshot_of_an_earlier_run[snapshot-stays], tests/test_outputs.py::test_refuse_leaves_no_snapshot_of_an_earlier_run, tests/test_outputs.py::test_capture_follows_no_link_to_the_output_folder
origin: src/kraft/outputs.py §capture

## REQ a-copy-takes-no-more-than-the-file-measured

WHEN the system copies a worker's file, it SHALL copy no more than the size its descriptor reported at open, and refuse a file over the caller's limit or with another hard link when asked.
enforced-by: tests/test_item_paths.py::test_copy_in_takes_a_limit_the_caller_sets, tests/test_item_paths.py::test_copy_in_can_refuse_a_file_with_another_hard_link, tests/test_item_paths.py::test_a_file_that_grows_after_it_was_measured_is_copied_only_to_its_measured_size
origin: src/kraft/item_paths.py §copy_in

## REQ a-variable-is-read-from-the-writers-that-declare-it

WHEN the system looks a variable up in the snapshots of several writers, it SHALL take the last writer's value, and only from a writer whose current declaration lists it.
enforced-by: tests/test_outputs.py::test_lookup_variable[later-writer-wins], tests/test_outputs.py::test_lookup_variable[found-in-an-earlier-writer], tests/test_outputs.py::test_lookup_variable[undeclared-key], tests/test_outputs.py::test_lookup_variable[absent-snapshot], tests/test_outputs.py::test_lookup_variable_reads_the_current_declaration_not_the_old_snapshot
origin: src/kraft/outputs.py §lookup_variable

## REQ a-failed-capture-fails-the-writer

IF a capture fails for a session that ended done, THEN the system SHALL end the session failed.
enforced-by: tests/adapters/test_subprocess_outputs.py::test_capture_failure_fails_writer
origin: src/kraft/adapters/subprocess.py §capture_outputs

## REQ a-writer-whose-processes-outlive-it-fails

IF the sandbox recorded that processes of a writer outlived it, THEN the system SHALL end it failed and keep none of its outputs.
enforced-by: tests/adapters/test_subprocess_outputs.py::test_survivor_fails_writer, tests/adapters/test_subprocess_outputs.py::test_another_sessions_survivor_does_not_fail_this_writer
origin: src/kraft/adapters/subprocess.py §_outlived

## REQ a-capture-after-a-restart-uses-the-cap-the-session-launched-under

WHEN a writer ends across a restart, the system SHALL capture it under the names and size cap recorded when it launched.
enforced-by: tests/worker/test_reattach_outputs.py::test_capture_across_restart[launched-with-room], tests/worker/test_reattach_outputs.py::test_capture_across_restart[launched-with-less-than-it-wrote]
origin: src/kraft/worker/reattach.py §_exit_from_file

## REQ a-capture-is-made-once-per-session

WHEN a session that was captured before is captured again, the system SHALL return what the first capture concluded and leave the snapshot as it is.
enforced-by: tests/test_outputs.py::test_a_session_captured_twice_keeps_what_the_first_capture_concluded[kept], tests/test_outputs.py::test_a_session_captured_twice_keeps_what_the_first_capture_concluded[failed], tests/worker/test_reattach_outputs.py::test_a_capture_made_before_the_crash_is_not_made_again
origin: src/kraft/outputs.py §capture

## REQ a-machines-refusal-outlives-the-server

WHEN a machine reports that it dropped a writer's outputs for their size, the system SHALL record that beside the session, so a writer that ends after a restart still fails.
enforced-by: tests/remote/test_machine_outputs.py::test_machine_refuses_oversize_outputs_before_packing, tests/worker/test_reattach_outputs.py::test_a_machines_refusal_survives_the_restart
origin: src/kraft/remote/runs.py §_deliver

## REQ outputs-max-size-defaults-to-64m-and-stops-at-the-transfer-limit

The system SHALL read `storage.outputs.max_size` as a size defaulting to 64M, and refuse one above what a remote machine returns.
enforced-by: tests/test_policy_storage.py::test_outputs_max_size[default], tests/test_policy_storage.py::test_outputs_max_size[empty-block], tests/test_policy_storage.py::test_outputs_max_size[ten-megabytes], tests/test_policy_storage.py::test_outputs_max_size[the-remote-limit-itself], tests/test_policy_storage.py::test_outputs_max_size_refuses[above-the-remote-limit], tests/test_policy_storage.py::test_outputs_max_size_refuses[gigabyte], tests/test_policy_storage.py::test_outputs_max_size_refuses[bare-int], tests/test_policy_storage.py::test_outputs_max_size_refuses[zero]
origin: src/kraft/policy.py §StorageOutputsInput

## REQ a-launch-carries-the-outputs-limit

WHEN the system builds a launch context, it SHALL carry the policy's `storage.outputs.max_size`.
enforced-by: tests/api/test_deps_outputs.py::test_launch_carries_the_outputs_limit, tests/executor/test_dispatch_outputs.py::test_writer_launch[agent], tests/executor/test_dispatch_outputs.py::test_writer_launch[subprocess]
origin: src/kraft/api/deps.py §launch

## REQ a-remote-writer-has-an-output-folder-and-its-cap

WHEN the system sends a writer to a machine, it SHALL send the cap it launched under and have the machine make the writer's output folder, without making it a read-write entry of its own.
enforced-by: tests/remote/test_machine_outputs.py::test_jobspec_carries_cap_and_outputs_dir
origin: src/kraft/remote/launch.py §build

## REQ a-machine-drops-an-oversized-output-folder-before-packing

IF a writer's output folder on a machine is over the cap or the entry limit, THEN the machine SHALL not send it back, and the system SHALL fail the writer with the outputs message.
enforced-by: tests/remote/test_machine_outputs.py::test_machine_refuses_oversize_outputs_before_packing
origin: src/kraft/remote/machine.py §_finish

## REQ the-server-checks-the-cap-again-for-an-older-machine

IF a machine that ignores the cap sends back an output folder over it, THEN the system SHALL fail the writer in its own capture.
enforced-by: tests/remote/test_machine_outputs.py::test_server_rechecks_cap_from_old_machine
origin: src/kraft/outputs.py §capture

## REQ a-machine-deletes-the-outputs-of-a-delivered-job

WHEN the server says a job is delivered, the machine SHALL delete the job's output folder.
enforced-by: tests/remote/test_machine_outputs.py::test_delivered_deletes_machine_outputs
origin: src/kraft/remote/machine.py

## REQ a-remote-reader-is-given-its-snapshot-a-file-at-a-time

WHEN the system sends a reader to a machine, it SHALL send one read-only entry per regular file of each snapshot folder, following no link.
enforced-by: tests/remote/test_machine_outputs.py::test_remote_reader_gets_one_ro_entry_per_snapshot_file[plain], tests/remote/test_machine_outputs.py::test_remote_reader_gets_one_ro_entry_per_snapshot_file[space], tests/remote/test_machine_outputs.py::test_remote_reader_gets_one_ro_entry_per_snapshot_file[non-ascii]
origin: src/kraft/remote/files.py §regular_files
