# Intent: item files

Where a work item's files are written and read. Everything but the worktree
goes under `run/items/<id>/`; a file written before the upgrade is read where
it is. A worker writes only its own two session files and its item's `docs/`,
and Kraft never follows a link in anything a worker can write.

## REQ a-gate-document-is-written-to-its-items-docs

WHEN the system tells an agent where to write a gate document, it SHALL name
`run/items/<id>/docs/<kind>.md`.
enforced-by: tests/adapters/test_agent.py::test_the_system_prompt_carries_in_order[artifact-path], tests/adapters/test_agent.py::test_the_system_prompt_carries_in_order[mr-meta-fields], tests/executor/test_dispatch_documents.py::test_a_later_launch_is_told_where_the_document_an_earlier_one_wrote_is
origin: src/kraft/adapters/agent.py §artifact_path -- outside the repository, so nothing Kraft writes for itself can reach a merge request, and in the folder a sandbox mounts read-write.

## REQ a-session-s-files-are-in-its-own-folder

WHEN the system creates a session that is not an escalation turn, it SHALL put
the session's log in `run/items/<id>/sessions/<sid>/` and its result file in
that folder's `out/`.
enforced-by: tests/adapters/test_subprocess_sandbox_results.py::test_a_session_records_its_folder_and_mounts_only_its_out_files, tests/test_item_paths.py::test_a_session_s_worker_files_are_apart_from_kraft_s, tests/test_item_paths.py::test_a_session_s_exit_code_is_found_by_its_row[own-folder]
origin: src/kraft/adapters/subprocess.py §run_task -- a session's log, exit code and container id are in its own folder, never handed to the worker; only its `out/` holds what the worker writes.

## REQ a-filed-attachment-is-copied-to-its-items-folder

WHEN intake copies an attachment, the system SHALL copy it into
`run/items/<id>/filed/`.
enforced-by: tests/executor/test_entry.py::test_intake_copies_an_attachment_into_kraft_storage, tests/api/test_attachment_patch.py::test_an_attachment_reads_from_where_it_was_stored[filed], tests/api/test_attachment_patch.py::test_an_attachment_reads_from_where_it_was_stored[older-attachments], tests/api/test_attachment_patch.py::test_an_attachment_reads_from_where_it_was_stored[run-dir-spelled-otherwise], tests/index/test_service.py::test_documents_for_work_item_shows_an_unindexed_attachment[stored-under-a-moved-run-dir], tests/test_builtins.py::test_an_attachment_from_another_worktree_is_copied_from_its_source[run-dir-spelled-otherwise], tests/test_builtins.py::test_an_attachment_from_another_worktree_is_copied_from_its_source[older-run-dir-still-there]
origin: src/kraft/executor/entry.py §_store_attachments -- outside `docs/`, which a sandbox mounts read-write, so a worker cannot rewrite what a person filed; an older item's copy stays in `attachments/<id>/` and is read there.

## REQ a-document-is-read-from-the-new-place-first

WHEN the system reads a work item's gate document, it SHALL read
`run/items/<id>/docs/<kind>.md` if that exists, and otherwise
`.engineering/<kind>s/<id>.md` in the item's worktree.
enforced-by: tests/test_item_paths.py::test_a_document_is_read_from_the_new_place_first[both-new-wins], tests/test_item_paths.py::test_a_document_is_read_from_the_new_place_first[new-only], tests/test_item_paths.py::test_a_document_is_read_from_the_new_place_first[old-only], tests/test_item_paths.py::test_a_document_is_read_from_the_new_place_first[neither], tests/executor/test_gates_documents.py::test_the_gate_names_the_document_where_it_is[new-place], tests/executor/test_gates_documents.py::test_the_gate_names_the_document_where_it_is[older-worktree], tests/executor/test_gates_documents.py::test_the_gate_names_the_document_where_it_is[nothing-yet], tests/api/test_artifact.py::test_gate_artifact_names_the_file_the_hook_wrote[new-place], tests/api/test_artifact.py::test_gate_artifact_names_the_file_the_hook_wrote[older-worktree], tests/api/test_artifact.py::test_a_tasks_produced_document_reads_off_the_worktree_then_from_the_index[older-worktree], tests/test_progress.py::test_read_tasks_prefers_the_plan_attachment[docs-before-older-worktree], tests/adapters/forge/test_mr_meta.py::test_read_mr_meta_reads_every_field[docs], tests/adapters/test_agent.py::test_a_declared_artifact_guards_the_node[written-to-the-old-place-stays-green]
origin: src/kraft/item_paths.py §document -- the new place is read first because a newer revision is only ever written there; nothing is moved at upgrade, so the old place is the fallback rather than a per-item flag.

## REQ an-existing-sandbox-home-is-kept

WHILE `run/sandbox-home/<id>/` exists, the system SHALL use it as the item's
sandbox home.
enforced-by: tests/test_item_paths.py::test_an_existing_sandbox_home_is_kept[old-home-exists], tests/adapters/test_subprocess_sandbox_results.py::test_a_sandbox_mounts_its_item_s_home_and_nothing_kraft_keeps[old-home-exists], tests/adapters/test_agent_sandbox.py::test_a_sandboxed_item_gets_a_cli_config_dir_of_its_own[old-home-exists]
origin: src/kraft/item_paths.py §sandbox_home -- the old home holds the agent CLI's chats a paused session resumes from.

## REQ a-new-sandbox-home-is-in-the-items-folder

WHILE `run/sandbox-home/<id>/` does not exist, the system SHALL use
`run/items/<id>/sandbox-home/` as the item's sandbox home.
enforced-by: tests/test_item_paths.py::test_an_existing_sandbox_home_is_kept[no-old-home], tests/adapters/test_subprocess_sandbox_results.py::test_a_sandbox_mounts_its_item_s_home_and_nothing_kraft_keeps[no-old-home], tests/adapters/test_agent_sandbox.py::test_a_sandboxed_item_gets_a_cli_config_dir_of_its_own[no-old-home]
origin: src/kraft/item_paths.py §sandbox_home

## REQ an-escalation-thread-keeps-its-paths

WHILE an escalation thread continues, the system SHALL give each of its turns
the result and summary paths the thread's first turn was given.
enforced-by: tests/test_escalation_thread_files.py::test_a_thread_keeps_its_first_turn_s_paths_for_every_turn[before-upgrade], tests/test_escalation_thread_files.py::test_a_thread_keeps_its_first_turn_s_paths_for_every_turn[after-upgrade], tests/test_escalation_thread_files.py::test_a_resumed_turn_that_writes_where_it_remembers_is_done
origin: src/kraft/escalate.py §thread_files -- a thread is from before the upgrade when its first turn's log is not in a session folder; the log, because nothing rewrites it, where `_archive_last_turn` rewrites `result_path`.

## REQ kraft-records-the-summary-it-asked-for

WHEN a session exits with `summary.md` in its own or its thread's folder, the
system SHALL record that file as its summary.
enforced-by: tests/test_item_paths.py::test_kraft_records_only_a_summary_the_worker_wrote[written], tests/test_item_paths.py::test_kraft_records_only_a_summary_the_worker_wrote[left-empty], tests/adapters/test_subprocess_result_files.py::test_read_result_fields_is_the_one_place_the_field_list_lives[kraft-s-wins], tests/adapters/test_agent.py::test_agent_writes_session_summary_and_ref_lands_in_db, tests/test_escalation_thread_files.py::test_each_turns_result_and_summary_stay_readable_from_its_row, tests/worker/test_reattach_summaries.py::test_a_session_resolved_after_a_restart_records_the_summary_kraft_asked_for[written], tests/worker/test_reattach_summaries.py::test_a_session_resolved_after_a_restart_records_the_summary_kraft_asked_for[escalation-thread-s]
origin: src/kraft/adapters/subprocess.py §read_result_fields -- Kraft creates the summary empty to mount it, so it records the ref itself when the file is regular and non-empty; the worker's own ref is untrusted.

## REQ a-worker-cannot-name-a-summary-under-items

IF a worker reports a summary ref under `items/`, THEN the system SHALL NOT
record it.
enforced-by: tests/adapters/test_subprocess_result_files.py::test_read_result_fields_is_the_one_place_the_field_list_lives[worker-ref-under-items-refused], tests/adapters/test_subprocess_result_files.py::test_read_result_fields_is_the_one_place_the_field_list_lives[absolute-refused], tests/index/test_service_summaries.py::test_a_summary_is_read_only_from_its_own_session_s_folder[another-session-s], tests/index/test_service_summaries.py::test_a_summary_is_read_only_from_its_own_session_s_folder[another-item-s], tests/index/test_service_summaries.py::test_a_summary_is_read_only_from_its_own_session_s_folder[items-ref-in-the-worktree], tests/worker/test_reattach_summaries.py::test_a_session_resolved_after_a_restart_records_the_summary_kraft_asked_for[worker-ref-under-items-refused], tests/index/test_service_summaries.py::test_a_summary_is_read_only_from_its_own_session_s_folder[another-thread-s]
origin: src/kraft/index/service.py §_read_summary -- `session_summary_ref` is reported by the worker, and the index reads what it names into a search every repo's agents can query.

## REQ the-session-folder-is-not-mounted

WHILE a sandboxed session runs, the system SHALL NOT mount any session folder
as a folder.
enforced-by: tests/worker/backends/test_docker_session_files.py::test_a_session_writes_only_its_own_two_files_and_its_item_s_docs, tests/adapters/test_subprocess_sandbox_results.py::test_a_session_records_its_folder_and_mounts_only_its_out_files
origin: src/kraft/worker/backends/docker.py §docker_argv -- the session folder holds the log Kraft reads usage and rate limits from and the exit code the host writes with a plain `>`.

## REQ a-worker-writes-only-its-own-two-files-and-docs

WHILE a sandboxed session runs, the system SHALL NOT mount read-write any path
under `run/items/<id>/` other than its own `result.json`, its own
`summary.md`, its item's `docs/` and its item's sandbox home.
enforced-by: tests/worker/backends/test_docker_session_files.py::test_a_session_writes_only_its_own_two_files_and_its_item_s_docs, tests/adapters/test_subprocess_sandbox_results.py::test_a_session_records_its_folder_and_mounts_only_its_out_files
origin: src/kraft/adapters/subprocess.py §run_task -- each file a single-file bind mount, which cannot be removed or swapped for a symlink from inside the container.

## REQ a-harness-is-granted-only-out-and-docs

WHEN the system names the folders a harness may write outside its worktree, it
SHALL name only the session's `out/` folder, its item's `docs/` and the
repository's git folder. An escalation thread started before the upgrade
keeps the grant it started with, the shared `results/` folder, until it ends.
enforced-by: tests/adapters/test_agent_writable_dirs.py::test_a_codex_worktree_launch_may_write_its_results_and_its_git_dir, tests/adapters/test_agent_writable_dirs.py::test_outside_a_repo_only_the_results_dir_is_granted
origin: src/kraft/adapters/agent.py §_writable_dirs -- for codex this is a real OS sandbox's writable roots, so granting the session folder would let even an unsandboxed worker plant a link where the host writes.

## REQ no-read-of-a-worker-written-file-follows-a-link

WHEN the system reads a file in a folder a worker can write, it SHALL NOT
follow a symbolic link.
enforced-by: tests/test_item_paths.py::test_a_document_is_read_from_the_new_place_first[symlink-is-none], tests/test_item_paths.py::test_a_document_is_read_from_the_new_place_first[fifo-is-none], tests/test_item_paths.py::test_a_document_is_read_from_the_new_place_first[old-folder-symlink-is-none], tests/test_item_paths.py::test_kraft_records_only_a_summary_the_worker_wrote[symlink], tests/test_item_paths.py::test_kraft_records_only_a_summary_the_worker_wrote[fifo], tests/test_item_paths.py::test_copying_a_worker_s_file_follows_no_link[source-symlink], tests/test_item_paths.py::test_copying_a_worker_s_file_follows_no_link[source-folder-symlink], tests/adapters/test_subprocess_result_files.py::test_resolve_reads_the_result_file_over_the_exit_code[symlink-is-unreadable], tests/index/test_service_summaries.py::test_a_summary_in_an_item_folder_is_never_read_through_a_link[symlink], tests/index/test_service_summaries.py::test_a_summary_in_an_item_folder_is_never_read_through_a_link[folder-symlink], tests/test_progress.py::test_read_tasks_without_a_plan_file_is_empty[symlink], tests/test_progress.py::test_read_tasks_without_a_plan_file_is_empty[fifo], tests/adapters/forge/test_mr_meta.py::test_read_mr_meta_refuses_a_symlink_out_of_the_worktree[docs], tests/api/test_artifact.py::test_a_symlink_out_of_the_worktree_is_a_404[pending-gate], tests/api/test_artifact.py::test_a_symlink_out_of_the_worktree_is_a_404[produced], tests/test_storage.py::test_measure_does_not_follow_a_symlink[item-sandbox-home], tests/test_storage.py::test_measure_does_not_follow_a_symlink[item-docs], tests/test_storage.py::test_measure_counts_what_a_folder_held_when_it_was_opened[before-open], tests/test_storage.py::test_measure_counts_what_a_folder_held_when_it_was_opened[before-listing], tests/adapters/test_subprocess_result_files.py::test_resolve_exit_file_missing_or_garbage_is_none[symlink-is-unreadable], tests/adapters/test_subprocess_result_files.py::test_resolve_exit_file_missing_or_garbage_is_none[fifo-is-unreadable], tests/executor/test_dispatch_exit_code.py::test_an_exit_code_is_read_without_following_a_link_or_waiting[symlink-is-unreadable], tests/executor/test_dispatch_exit_code.py::test_an_exit_code_is_read_without_following_a_link_or_waiting[fifo-is-unreadable]
origin: src/kraft/item_paths.py -- every read goes through `worker.worktree_read`, which opens one component at a time with `O_NOFOLLOW` and opens the file non-blocking, so a planted FIFO cannot hang the server either.

## REQ no-write-into-a-worker-folder-follows-a-link

WHEN the system writes into a folder a worker can write, it SHALL NOT follow or
replace a symbolic link.
enforced-by: tests/test_item_paths.py::test_a_planted_name_in_docs_is_neither_followed_nor_replaced, tests/test_item_paths.py::test_copying_a_worker_s_file_follows_no_link[dest-symlink], tests/test_item_paths.py::test_copying_a_worker_s_file_follows_no_link[dest-folder-symlink], tests/adapters/test_subprocess_sandbox_results.py::test_a_worker_s_files_are_created_never_written_through_a_link[symlink-replaced], tests/adapters/test_agent_sandbox.py::test_a_link_in_the_sandbox_home_is_never_written_through[config-folder], tests/adapters/test_agent_sandbox.py::test_a_link_in_the_sandbox_home_is_never_written_through[harness-folder], tests/adapters/test_agent_sandbox.py::test_a_link_in_the_sandbox_home_is_never_written_through[config-file], tests/adapters/test_agent_sandbox.py::test_a_link_in_the_sandbox_home_is_never_written_through[temporary-file], tests/api/test_abandon_item_folder.py::test_abandoning_removes_the_item_s_folder_and_nothing_a_link_in_it_points_at, tests/test_escalation_thread_files.py::test_a_folder_planted_at_the_thread_s_summary_does_not_stop_the_next_turn, tests/test_item_paths.py::test_a_folder_its_owner_cannot_write_is_still_removed[planted-summary], tests/test_item_paths.py::test_a_folder_its_owner_cannot_write_is_still_removed[worker-files]
origin: src/kraft/item_paths.py §copy_in -- each folder on the way is opened with `open_dir_no_symlinks` and the file with `O_CREAT | O_EXCL | O_NOFOLLOW` relative to it, so a name already there is refused rather than written through.

## REQ history-and-filed-are-not-mounted

WHILE a sandboxed session runs, the system SHALL NOT mount its item's
`history/` or `filed/`.
enforced-by: tests/adapters/test_subprocess_sandbox_results.py::test_a_sandbox_mounts_its_item_s_home_and_nothing_kraft_keeps[old-home-exists], tests/adapters/test_subprocess_sandbox_results.py::test_a_sandbox_mounts_its_item_s_home_and_nothing_kraft_keeps[no-old-home]
origin: src/kraft/adapters/subprocess.py §run_task -- a sandbox is handed `docs/` and the session's two `out/` files to write, and nothing else of the item's folder, so a worker cannot rewrite what a person filed or a version a gate decided on.

## REQ every-agent-is-told-its-items-documents

WHEN the system launches an agent, it SHALL name in its context the absolute
path of each of its item's documents that exists.
enforced-by: tests/adapters/test_agent_documents.py::test_every_agent_is_told_where_its_item_s_documents_are[new-place], tests/adapters/test_agent_documents.py::test_every_agent_is_told_where_its_item_s_documents_are[older-worktree], tests/adapters/test_agent_documents.py::test_every_agent_is_told_where_its_item_s_documents_are[attached], tests/adapters/test_agent_documents.py::test_every_agent_is_told_where_its_item_s_documents_are[none-yet], tests/executor/test_dispatch_documents.py::test_a_later_launch_is_told_where_the_document_an_earlier_one_wrote_is
origin: src/kraft/adapters/agent.py §documents_note -- built in `run_agent_task`, so a fix loop, an override, an escalation turn and a gate review are told too; a later node used to find a document by its place in the worktree, where it no longer is.

## REQ a-revised-document-keeps-its-earlier-version

WHEN the system dispatches a task that produces a kind whose document exists,
it SHALL first copy that document to `history/<kind>.<n>.md`.
enforced-by: tests/test_item_paths.py::test_a_revised_document_keeps_its_earlier_versions, tests/test_item_paths.py::test_an_old_document_is_seeded_into_docs_before_its_revision[own-worktree], tests/test_item_paths.py::test_an_old_document_is_seeded_into_docs_before_its_revision[member-worktree], tests/executor/test_dispatch_documents.py::test_a_re_dispatch_keeps_the_version_it_revises[steered], tests/executor/test_dispatch_documents.py::test_a_re_dispatch_keeps_the_version_it_revises[retried], tests/executor/test_dispatch_documents.py::test_a_re_dispatch_keeps_the_version_it_revises[older-worktree]
origin: src/kraft/item_paths.py §keep_version -- the document stays where it is, so the agent revises it in place and a failed re-run still leaves the gate one; an old document is also seeded into `docs/` so the revise note and the write-to path name one file.

## REQ archiving-removes-the-sandbox-home

WHEN a work item is archived, the system SHALL remove its sandbox home.
enforced-by: tests/api/test_archive.py::test_archiving_keeps_the_item_s_folder_but_its_sandbox_home[new-home], tests/api/test_archive.py::test_archiving_keeps_the_item_s_folder_but_its_sandbox_home[old-home], tests/test_item_paths.py::test_a_folder_its_owner_cannot_write_is_still_removed[archived-home]
origin: src/kraft/worker/backends/docker.py §release -- both places, since an older item's home is in `sandbox-home/<id>/`; the home is a cache of the agent CLI's, and the worktree it served is gone.

## REQ archiving-keeps-the-items-folder

WHEN a work item is archived, the system SHALL keep its `docs/`, `history/`,
`filed/` and `sessions/`.
enforced-by: tests/api/test_archive.py::test_archiving_keeps_the_item_s_folder_but_its_sandbox_home[new-home], tests/api/test_archive.py::test_archiving_keeps_the_item_s_folder_but_its_sandbox_home[old-home]
origin: src/kraft/api/routes/lifecycle.py §_forget_sandbox -- archiving removes the worktree and the sandbox home only, so an archived item's documents and session files stay readable where they were written.

## REQ abandoning-removes-the-items-folder

WHEN a work item is abandoned, the system SHALL remove `run/items/<id>/`.
enforced-by: tests/api/test_abandon_item_folder.py::test_abandoning_removes_the_item_s_folder_and_nothing_a_link_in_it_points_at, tests/api/test_lifecycle.py::test_abandon_reclaims_the_attachment_storage, tests/api/test_abandon_item_folder.py::test_abandoning_removes_the_item_s_folder_off_the_event_loop, tests/test_item_paths.py::test_a_folder_its_owner_cannot_write_is_still_removed[abandoned-item]
origin: src/kraft/api/routes/lifecycle.py §abandon_work_item -- `item_paths.remove_tree` walks by folder descriptor and removes a link without following it, so a link a worker planted in `docs/` or the sandbox home takes nothing outside with it.

## REQ storage-counts-item-sandbox-homes

The system SHALL count every `run/items/*/sandbox-home` in the governed
storage figure, under its item.
enforced-by: tests/test_storage.py::test_measure_counts_an_item_s_sandbox_home_under_the_item[items-folder], tests/test_storage.py::test_measure_counts_an_item_s_sandbox_home_under_the_item[items-is-a-link], tests/test_storage.py::test_refresh_drops_an_item_removed_while_it_walked[item-sandbox-home]
origin: src/kraft/storage.py §measure -- only the sandbox home is governed, since archiving removes it; `docs/`, `history/` and `filed/` count as attachments and `sessions/` as logs, so the Storage page still adds up.

## REQ doctor-counts-worktrees-with-old-documents

WHEN `kraft admin doctor` runs, the system SHALL report how many worktrees
hold a `.engineering/` folder.
enforced-by: tests/test_doctor_documents.py::test_doctor_counts_worktrees_with_old_documents[none], tests/test_doctor_documents.py::test_doctor_counts_worktrees_with_old_documents[two], tests/cli/test_doctor.py::test_doctor_on_a_live_instance_reaches_every_check
origin: src/kraft/doctor.py §_old_documents_check -- a warning, never a failure: the documents are read where they are, and the count reaching zero is when the `.engineering/` fallbacks can go.

## REQ an-items-folder-that-is-a-link-is-followed

WHILE `run/items` is a symbolic link, the system SHALL read, write and measure
each item's files through it, and follow no link below the item's own folder.
enforced-by: tests/test_item_paths.py::test_a_run_items_folder_that_is_a_link_is_followed, tests/index/test_service_summaries.py::test_a_summary_is_read_only_from_its_own_session_s_folder[run-items-is-a-link], tests/test_storage.py::test_measure_counts_an_item_s_sandbox_home_under_the_item[items-is-a-link]
origin: src/kraft/item_paths.py §locate -- `run/items` and an item's folder are Kraft's, and may be moved to another disk; only what is below an item's folder is a worker's to plant a link in, so a no-follow walk starts there.

## REQ an-older-thread-s-summary-is-the-worker-s-to-name

WHILE an escalation thread started before the upgrade continues, the system
SHALL NOT record the thread's shared summary for a turn whose worker reported
none.
enforced-by: tests/test_item_paths.py::test_kraft_records_only_a_summary_the_worker_wrote[older-thread], tests/test_escalation_thread_files.py::test_a_turn_starts_with_no_result_file_from_the_last[before-upgrade], tests/test_escalation_thread_files.py::test_a_thread_keeps_its_first_turn_s_paths_for_every_turn[before-upgrade]
origin: src/kraft/item_paths.py §recorded_summary -- an older thread's summary stays in the worktree for every turn, so one turn's is still there when the next writes none.

## REQ keeping-a-version-never-stops-a-dispatch

IF a document cannot be kept in `history/`, or is larger than the most Kraft
reads of a document, THEN the system SHALL dispatch the task without keeping a
version.
enforced-by: tests/test_item_paths.py::test_keeping_a_version_is_never_a_reason_to_stop[oversized], tests/test_item_paths.py::test_keeping_a_version_is_never_a_reason_to_stop[history-not-a-folder], tests/test_item_paths.py::test_keeping_a_version_is_never_a_reason_to_stop[item-not-writable], tests/test_item_paths.py::test_an_oversized_worker_file_is_not_copied
origin: src/kraft/item_paths.py §keep_version -- a worker writes `docs/` at any size, and a version is copied again on every retry; keeping one is a convenience.

## REQ storage-governs-what-a-worker-writes

The system SHALL count every item's `docs/` and every session's `out/` in the
governed storage figure.
enforced-by: tests/test_storage.py::test_measure_counts_an_item_s_sandbox_home_under_the_item[items-folder], tests/test_storage.py::test_report_joins_the_measurement_to_the_work_items, tests/api/test_abandon_item_folder.py::test_abandoning_takes_the_item_s_documents_off_the_storage_figure, tests/test_storage.py::test_refresh_drops_the_documents_of_an_item_abandoned_while_it_walked[abandoned], tests/test_storage.py::test_refresh_drops_the_documents_of_an_item_abandoned_while_it_walked[archived]
origin: src/kraft/storage.py §measure -- both are a worker's to write at any size outside its worktree, so the quota and limit must see them.

## REQ an-oversized-worker-file-reads-as-unreadable

IF a file a worker can write is larger than the most Kraft reads of one whole,
THEN the system SHALL treat it as unreadable.
enforced-by: tests/worker/test_worktree_read.py::test_an_oversized_worker_file_reads_as_unreadable, tests/index/test_ingest.py::test_an_oversized_document_is_not_read
origin: src/kraft/worker/worktree_read.py §read_no_follow -- a result file or summary is read several times a session, and a worker can write one of any size; read no further than one byte past the cap.

## REQ the-storage-walk-holds-few-descriptors

WHEN the system measures or removes a folder a worker can write, it SHALL hold
a bounded number of open descriptors however deep the folder nests, take time
linear in its folders, and reach what is at the bottom.
enforced-by: tests/test_storage.py::test_a_deep_tree_is_counted_within_a_low_open_file_limit, tests/test_storage.py::test_a_deep_tree_is_counted_in_time_linear_in_its_folders, tests/test_item_paths.py::test_a_deep_tree_is_removed_within_a_low_open_file_limit, tests/worker/test_tree_walk.py::test_a_folder_gone_back_up_to_by_dotdot_is_the_one_come_down_through[in-place], tests/worker/test_tree_walk.py::test_a_folder_gone_back_up_to_by_dotdot_is_the_one_come_down_through[moved-out], tests/worker/test_tree_walk.py::test_a_walk_holds_at_least_the_folder_it_is_in[zero], tests/worker/test_tree_walk.py::test_a_walk_holds_at_least_the_folder_it_is_in[negative]
origin: src/kraft/worker/tree_walk.py §walk -- a worker can nest folders far deeper than the daemon's open-file limit; one descriptor per level starves the daemon's sockets, database and logs, and reopening each deep folder from the top is quadratic. The walk keeps the deepest few, goes back up by `..`, and stops where `..` is not the folder it came down through.

## REQ removing-a-workers-folder-follows-no-link

WHEN the system removes a folder a worker can write, it SHALL remove nothing
outside it, even when the worker swaps a folder in it for a link while it is
being removed, and SHALL remove a folder there its owner cannot write or list.
enforced-by: tests/test_item_paths.py::test_a_folder_swapped_for_a_link_while_it_is_removed_takes_nothing_outside[read-only], tests/test_item_paths.py::test_a_folder_swapped_for_a_link_while_it_is_removed_takes_nothing_outside[mode-0], tests/test_item_paths.py::test_a_folder_swapped_for_a_link_while_it_is_removed_takes_nothing_outside[mode-0-folder-itself], tests/test_item_paths.py::test_a_folder_its_owner_cannot_write_is_still_removed[abandoned-item], tests/test_item_paths.py::test_a_folder_its_owner_cannot_write_is_still_removed[archived-home], tests/test_item_paths.py::test_a_folder_its_owner_cannot_write_is_still_removed[planted-summary], tests/test_item_paths.py::test_a_folder_its_owner_cannot_write_is_still_removed[worker-files]
origin: src/kraft/item_paths.py §remove_tree -- a path-based retry (`chmod`, `unlink`, `rmtree` by full path) resolves every component again, so a worker that swaps `docs/a` for a link to the daemon's home between the failure and the retry has the daemon delete its own files; every step is by a descriptor, and a mode-0 folder is unlocked through `O_PATH` and `/proc/self/fd`, or `fchmodat(AT_SYMLINK_NOFOLLOW)`, never by path.

## REQ auto-cleanup-archives-only-for-what-archiving-frees

WHILE `auto_cleanup` archives items to make room, the system SHALL stop once
the part of usage archiving can free is at or under the quota, and SHALL name
in its log the items whose documents keep usage over the limit.
enforced-by: tests/test_storage.py::test_tick_archives_only_down_to_what_archiving_can_free[documents-keep-it-over], tests/test_storage.py::test_tick_archives_only_down_to_what_archiving_can_free[worktrees-keep-it-over]
origin: src/kraft/storage.py §tick -- items' documents are governed but archiving keeps them, so a worker that filled its `docs/` would otherwise have every finished item archived without the figure coming down.
