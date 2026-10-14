# Intent: item identity

How a work item is named, and how a name a person types finds it. A value
that equals an item's id names that item; a start of one, 4 characters or
more, names it when it is the start of no other.

## REQ an-id-prefix-names-its-item
WHEN a work item id given to the API equals no item's id and is a start of 4 or more characters of exactly one, the system SHALL act on that item.
enforced-by: tests/api/test_item_ids.py::test_an_id_or_its_unique_start_names_the_item[unique-start], tests/api/test_item_ids.py::test_an_action_on_the_start_of_an_id_acts_on_the_whole_item, tests/api/test_lifecycle_bulk.py::test_bulk_archive_takes_only_ended_items[start], tests/api/test_storage.py::test_preview_takes_the_start_of_an_id_and_names_an_ambiguous_one, tests/api/test_work_items_after.py::test_what_an_item_may_be_filed_after[completed-by-its-start], tests/api/test_lifecycle_blocked.py::test_unblock_drops_what_the_item_waits_on_and_it_is_released[by-its-start], tests/cli/test_item.py::test_archive_needs_a_yes_and_then_archives[start], tests/cli/test_watching.py::test_events_follow_filters_by_item_and_stops_on_completion[start]
origin: src/kraft/api/item_ids.py §whole_work_item_id

## REQ an-exact-id-wins-over-a-prefix
WHEN a work item id given to the API equals one item's id and is the start of another's, the system SHALL act on the item it equals.
enforced-by: tests/api/test_item_ids.py::test_an_id_or_its_unique_start_names_the_item[exact-wins], tests/store/test_work_items.py::test_ids_in_hand_and_stored_ids_are_matched_alike[exact-wins]
origin: src/kraft/store/work_items.py §match_work_item_ids

## REQ an-ambiguous-prefix-is-refused
IF a work item id given to the API equals no item's id and is a start of 4 or more characters of more than one, THEN the system SHALL refuse it and name every match.
enforced-by: tests/api/test_item_ids.py::test_an_id_or_its_unique_start_names_the_item[ambiguous], tests/api/test_lifecycle_bulk.py::test_bulk_reports_an_ambiguous_start_and_goes_on, tests/api/test_storage.py::test_preview_takes_the_start_of_an_id_and_names_an_ambiguous_one, tests/api/test_work_items_after.py::test_an_item_cannot_be_filed_after_an_ambiguous_start, tests/api/test_lifecycle_blocked.py::test_unblock_refuses_an_id_that_is_not_a_dependency[ambiguous-start]
origin: src/kraft/api/item_ids.py §whole_work_item_id

## REQ a-short-value-is-not-a-prefix
IF a work item id given to the API is shorter than 4 characters and equals no item's id, THEN the system SHALL answer it as unknown.
enforced-by: tests/api/test_item_ids.py::test_an_id_or_its_unique_start_names_the_item[short]
origin: src/kraft/store/work_items.py §match_work_item_ids

## REQ a-prefix-is-lowercase
IF a work item id given to the API has a character outside `[0-9a-z]` and equals no item's id, THEN the system SHALL answer it as unknown.
enforced-by: tests/api/test_item_ids.py::test_an_id_or_its_unique_start_names_the_item[uppercase], tests/api/test_item_ids.py::test_an_id_or_its_unique_start_names_the_item[outside-chars-start], tests/store/test_work_items.py::test_ids_in_hand_and_stored_ids_are_matched_alike[uppercase]
origin: src/kraft/store/work_items.py §match_work_item_ids

## REQ a-prefix-is-resolved-only-for-an-admitted-caller
IF a caller the perimeter or authentication refuses names a work item by a start of its id, THEN the system SHALL refuse it as it refuses any other request, without resolving the id.
enforced-by: tests/api/test_perimeter.py::test_a_forged_navigation_header_cannot_write
origin: src/kraft/api/__init__.py

## REQ a-prefix-cannot-pass-the-self-action-guard
IF a worker names its own work item by a start of its id, THEN the system SHALL refuse the action as it refuses the whole id.
enforced-by: tests/api/test_item_ids.py::test_a_worker_naming_its_own_item_by_its_start_is_refused, tests/client/test_guard.py::test_a_worker_cannot_act_on_its_own_item[eight], tests/client/test_guard.py::test_a_worker_cannot_act_on_its_own_item[four]
origin: src/kraft/api/item_ids.py §whole_work_item_id, src/kraft/client/context.py §_forbid_self_action

## REQ an-id-is-drawn-from-lowercase-crockford-base32
WHEN the system draws a work item or session id, it SHALL draw every character from `0123456789abcdefghjkmnpqrstvwxyz`.
enforced-by: tests/test_ids.py::test_an_id_is_drawn_from_lowercase_crockford_base32[work-item], tests/test_ids.py::test_an_id_is_drawn_from_lowercase_crockford_base32[session]
origin: src/kraft/ids.py §mint

## REQ id-minting-gives-up-loudly
IF ten candidate ids in a row are taken, THEN the system SHALL refuse to create the item or session with an error that says ten candidates were taken.
enforced-by: tests/test_ids.py::test_ten_taken_candidates_in_a_row_give_up_and_say_why
origin: src/kraft/ids.py §mint

## REQ work-item-id-is-eight-characters
WHEN intake files a work item, the system SHALL give it an id of 8 characters.
enforced-by: tests/executor/test_entry_ids.py::test_intake_gives_a_new_item_an_8_character_id
origin: src/kraft/executor/entry.py §intake

## REQ a-work-item-id-is-not-the-start-of-another
IF a candidate work item id equals an existing item's id or is the start of one, THEN the system SHALL draw another.
enforced-by: tests/executor/test_entry_ids.py::test_intake_draws_again_when_the_first_id_is_taken[equal-id], tests/executor/test_entry_ids.py::test_intake_draws_again_when_the_first_id_is_taken[longer-id-it-starts]
origin: src/kraft/store/work_items.py §work_item_id_taken

## REQ a-work-item-id-names-no-leftover-folder
IF a folder `run/items/<c>`, `run/worktrees/<c>`, `run/attachments/<c>` or `run/sandbox-home/<c>` exists for a candidate work item id `c`, THEN the system SHALL draw another.
enforced-by: tests/executor/test_entry_ids.py::test_intake_draws_again_when_the_first_id_is_taken[item-folder], tests/executor/test_entry_ids.py::test_intake_draws_again_when_the_first_id_is_taken[worktree-folder], tests/executor/test_entry_ids.py::test_intake_draws_again_when_the_first_id_is_taken[attachments-folder], tests/executor/test_entry_ids.py::test_intake_draws_again_when_the_first_id_is_taken[sandbox-home-folder]
origin: src/kraft/executor/entry.py §work_item_id_taken_on_disk

## REQ a-work-item-id-ends-no-kraft-branch
IF a ref under `refs/heads/kraft/` or `refs/remotes/origin/kraft/` in the item's repo is named a candidate work item id or ends in `-` and that id, THEN the system SHALL draw another.
enforced-by: tests/executor/test_entry_ids.py::test_intake_draws_again_when_the_first_id_is_taken[plain-branch], tests/executor/test_entry_ids.py::test_intake_draws_again_when_the_first_id_is_taken[titled-branch], tests/executor/test_entry_ids.py::test_intake_draws_again_when_the_first_id_is_taken[remote-branch], tests/executor/test_entry_ids.py::test_intake_draws_again_when_the_first_id_is_taken[titled-remote-branch], tests/executor/test_entry_ids.py::test_intake_keeps_an_id_no_kraft_branch_ends_with[longer-name], tests/executor/test_entry_ids.py::test_intake_keeps_an_id_no_kraft_branch_ends_with[outside-kraft], tests/executor/test_entry_ids.py::test_intake_keeps_an_id_no_kraft_branch_ends_with[a-tag]
origin: src/kraft/executor/entry.py §work_item_id_taken_on_disk

## REQ session-id-is-six-characters
WHEN a session is created, the system SHALL give it an id of 6 characters.
enforced-by: tests/store/test_session_ids.py::test_a_new_session_id_is_six_characters_its_item_has_not_used, tests/executor/test_raised_task_sessions.py::test_a_task_that_raises_leaves_its_session_and_the_reason[builtin-error-before-its-row], tests/executor/test_raised_task_sessions.py::test_a_task_that_raises_leaves_its_session_and_the_reason[forge-after-its-row]
origin: src/kraft/store/sessions.py §new_session_id

## REQ session-id-is-unique-within-its-item
WHEN a session is created, the system SHALL give it an id no other session of the same work item has.
enforced-by: tests/store/test_session_ids.py::test_a_new_session_id_is_six_characters_its_item_has_not_used, tests/store/test_session_ids.py::test_a_session_id_another_item_has_is_free_for_this_one
origin: src/kraft/store/sessions.py §new_session_id

## REQ two-items-may-share-a-session-id
The system SHALL accept a session whose id another work item's session already has.
enforced-by: tests/store/test_session_ids.py::test_two_items_may_each_have_a_session_with_the_same_id, tests/test_db_session_key.py::test_the_session_key_migration_keeps_every_row_and_lets_items_share_an_id
origin: src/kraft/db.py §worker_sessions

## REQ a-session-is-looked-up-with-its-item
WHEN the system reads or writes a session it holds the work item of, it SHALL find it by its work item id and its session id together.
enforced-by: tests/store/test_session_ids.py::test_a_lookup_finds_only_its_own_item_s_session[exited], tests/store/test_session_ids.py::test_a_lookup_finds_only_its_own_item_s_session[running], tests/store/test_session_ids.py::test_a_lookup_finds_only_its_own_item_s_session[unknown], tests/store/test_session_ids.py::test_a_lookup_finds_only_its_own_item_s_session[escalation-stopped], tests/store/test_session_ids.py::test_a_lookup_finds_only_its_own_item_s_session[item-paused], tests/store/test_session_ids.py::test_a_lookup_finds_only_its_own_item_s_session[egress], tests/api/test_board_stop.py::test_each_item_reads_its_own_escalation_turn_of_a_shared_session_id
origin: src/kraft/store/sessions.py §session_status, src/kraft/api/routes/board.py §_stop_episode

## REQ a-session-is-told-its-qualified-reference
WHEN the system launches an agent session, it SHALL set `KRAFT_SESSION_ID` to `<wid>/<sid>`, its work item id and its session id.
enforced-by: tests/adapters/test_agent.py::test_identify_as_worker_sets_the_work_item_env_var[a-worker-by-default], tests/adapters/test_agent.py::test_identify_as_worker_sets_the_work_item_env_var[an-escalation-turn-is-not-a-worker], tests/test_worker_identity.py::test_the_session_environment_carries_the_session_id
origin: src/kraft/adapters/agent.py §run_agent_task

## REQ the-caller-session-is-qualified
WHEN the system reads the calling session from `X-Kraft-Session-Id`, it SHALL read a value `<wid>/<sid>` as session `sid` of work item `wid`, both matched whole.
enforced-by: tests/api/test_deps_session_ref.py::test_the_calling_session_is_read_with_its_item[own-item-s-session], tests/api/test_deps_session_ref.py::test_the_calling_session_is_read_with_its_item[same-id-other-item], tests/api/test_deps_session_ref.py::test_the_calling_session_is_read_with_its_item[other-item-s-session], tests/api/test_deps_session_ref.py::test_the_calling_session_is_read_with_its_item[own-item-named-by-its-start], tests/api/test_deps_session_ref.py::test_the_calling_session_is_read_with_its_item[pre-upgrade-qualified], tests/api/test_deps_session_ref.py::test_the_calling_session_is_read_with_its_item[header-item-by-its-start-names-no-session], tests/api/test_deps_session_ref.py::test_the_calling_session_is_read_with_its_item[header-item-in-another-case-names-no-session], tests/api/test_retry_paths.py::test_an_escalation_retrying_its_own_item_is_told_apart_by_item_and_id[qualified], tests/api/test_retry_paths.py::test_an_escalation_retrying_its_own_item_is_told_apart_by_item_and_id[same-id-other-item]
origin: src/kraft/api/deps.py §caller_sessions, src/kraft/api/routes/lifecycle.py §_retry

## REQ a-bare-caller-session-is-a-pre-upgrade-one
WHEN `X-Kraft-Session-Id` is a bare session id of 32 characters, the system SHALL read it as every session with that id, the self-action guard SHALL refuse the action if any of them belongs to the target work item, and a thread reply SHALL be refused unless exactly one session has it.
enforced-by: tests/api/test_deps_session_ref.py::test_the_calling_session_is_read_with_its_item[pre-upgrade-bare], tests/api/test_deps_session_ref.py::test_the_calling_session_is_read_with_its_item[bare-id-two-items-share], tests/api/test_retry_paths.py::test_an_escalation_retrying_its_own_item_is_told_apart_by_item_and_id[bare], tests/client/test_permission_request.py::test_the_ask_goes_to_the_session_kraft_session_id_names[pre-upgrade-bare], tests/api/test_review.py::test_reply_door_refuses_humans_and_other_items
origin: src/kraft/api/deps.py §caller_sessions, src/kraft/api/deps.py §forbid_self_action, src/kraft/api/routes/review.py §agent_reply

## REQ a-malformed-caller-session-is-refused
IF `X-Kraft-Session-Id` or `KRAFT_SESSION_ID` is neither a 32-character session id nor `<wid>/<sid>` with both halves present, THEN the system SHALL refuse the request.
enforced-by: tests/api/test_deps_session_ref.py::test_the_calling_session_is_read_with_its_item[bare-per-item-id], tests/api/test_deps_session_ref.py::test_the_calling_session_is_read_with_its_item[bare-start-of-a-pre-upgrade-id], tests/test_ids.py::test_a_malformed_session_reference_names_no_session[bare-per-item-id], tests/test_ids.py::test_a_malformed_session_reference_names_no_session[bare-one-short-of-pre-upgrade], tests/test_ids.py::test_a_malformed_session_reference_names_no_session[bare-one-past-pre-upgrade], tests/api/test_retry_paths.py::test_an_escalation_retrying_its_own_item_is_told_apart_by_item_and_id[bare-per-item-id], tests/client/test_permission_request.py::test_the_ask_goes_to_the_session_kraft_session_id_names[bare-per-item-id], tests/api/test_deps_session_ref.py::test_the_calling_session_is_read_with_its_item[malformed-no-session], tests/api/test_deps_session_ref.py::test_the_calling_session_is_read_with_its_item[malformed-no-item], tests/api/test_deps_session_ref.py::test_the_calling_session_is_read_with_its_item[malformed-a-second-slash], tests/test_ids.py::test_a_malformed_session_reference_names_no_session[empty], tests/test_ids.py::test_a_malformed_session_reference_names_no_session[no-session], tests/test_ids.py::test_a_malformed_session_reference_names_no_session[no-item], tests/test_ids.py::test_a_malformed_session_reference_names_no_session[a-second-slash], tests/test_ids.py::test_a_malformed_session_reference_names_no_session[an-empty-segment], tests/client/test_permission_request.py::test_the_ask_goes_to_the_session_kraft_session_id_names[malformed]
origin: src/kraft/ids.py §parse_session_ref, src/kraft/api/deps.py §caller_ref

## REQ nested-session-routes-name-the-item
The system SHALL serve a session's log, permission and permission-hook routes at `/work-items/{wid}/sessions/{sid}/...`, where `{wid}` may be a start of the item's id and `{sid}` is matched whole.
enforced-by: tests/api/test_session_routes.py::test_a_session_route_names_one_session[nested], tests/api/test_session_routes.py::test_a_session_route_names_one_session[nested-other-item], tests/api/test_session_routes.py::test_a_session_route_names_one_session[nested-wrong-item], tests/api/test_session_routes.py::test_a_session_route_names_one_session[nested-item-by-its-start], tests/api/test_session_routes.py::test_a_session_route_names_one_session[nested-session-never-by-its-start], tests/api/test_session_routes.py::test_a_session_route_names_one_session[nested-permission], tests/api/test_session_routes.py::test_a_session_route_names_one_session[nested-permission-hook], tests/cli/test_watching.py::test_logs_of_a_named_session_read_it_under_its_item[under-the-item], tests/client/test_permission_request.py::test_the_ask_goes_to_the_session_kraft_session_id_names[under-its-item]
origin: src/kraft/api/routes/sessions.py §_session_row, src/kraft/client/reads.py §permission_request, src/kraft/cli/view.py §_cmd_logs

## REQ flat-session-routes-answer-a-unique-id
WHEN a `/worker-sessions/{sid}/...` route is given an id exactly one session has, the system SHALL answer as the nested route would.
enforced-by: tests/api/test_session_routes.py::test_a_session_route_names_one_session[flat-unique], tests/api/test_session_routes.py::test_a_session_route_names_one_session[flat-unique-permission], tests/cli/test_watching.py::test_logs_of_a_named_session_read_it_under_its_item[no-item-to-name]
origin: src/kraft/api/routes/sessions.py §_flat_session_row

## REQ flat-session-routes-refuse-an-ambiguous-id
IF a `/worker-sessions/{sid}/...` route is given an id no session or more than one session has, THEN the system SHALL answer 404 naming the nested route.
enforced-by: tests/api/test_session_routes.py::test_a_session_route_names_one_session[flat-ambiguous], tests/api/test_session_routes.py::test_a_session_route_names_one_session[flat-ambiguous-permission], tests/api/test_session_routes.py::test_a_session_route_names_one_session[flat-ambiguous-permission-hook], tests/api/test_session_routes.py::test_a_session_route_names_one_session[flat-unknown]
origin: src/kraft/api/routes/sessions.py §_flat_session_row

## REQ the-channel-admits-only-its-own-session
IF a sandboxed session's channel is asked for a nested session route naming another work item or another session, THEN the system SHALL refuse it.
enforced-by: tests/worker/test_callback.py::test_a_nested_session_route_is_allowed_for_the_session_itself_only[/api/work-items/{}/sessions/{}/permission], tests/worker/test_callback.py::test_a_nested_session_route_is_allowed_for_the_session_itself_only[/api/work-items/{}/sessions/{}/permission-hook], tests/worker/test_egress_worker_api.py::test_a_verb_is_its_api_call_made_as_the_channel_session[hook]
origin: src/kraft/worker/callback.py §allowed, src/kraft/worker/egress.py §_caller

## REQ container-names-carry-the-item
WHEN the system names a container or volume for a session minted with a per-item id, it SHALL include the session's work item id and session id.
enforced-by: tests/worker/backends/test_docker_names.py::test_a_session_s_docker_names_carry_its_item[container], tests/worker/backends/test_docker_names.py::test_a_session_s_docker_names_carry_its_item[relay], tests/worker/backends/test_docker_names.py::test_a_session_s_docker_names_carry_its_item[relay-b], tests/worker/backends/test_docker_names.py::test_a_session_s_docker_names_carry_its_item[volume], tests/worker/backends/test_docker_names.py::test_a_launch_runs_and_joins_the_names_its_session_has[per-item-session], tests/worker/backends/test_docker_relay.py::test_sweep_keeps_a_live_sessions_relays_volume_and_certificate[per-item-session], tests/worker/test_reattach_oom.py::test_an_adopted_session_is_found_under_the_name_it_started_with[per-item-session]
origin: src/kraft/worker/backends/docker.py §container_name, src/kraft/worker/backends/docker.py §sweep, src/kraft/worker/reattach.py §reattach

## REQ a-session-s-channel-and-shared-files-carry-the-item
WHEN the system names the certificate, egress channel or socket folder of a session minted with a per-item id, or a file of its in a folder every work item shares, it SHALL derive the name from both its work item id and its session id.
enforced-by: tests/worker/test_channel.py::test_each_session_listens_in_a_socket_folder_of_its_own[same-id-two-items], tests/worker/test_channel.py::test_each_session_listens_in_a_socket_folder_of_its_own[two-sessions-of-an-old-item], tests/worker/test_channel.py::test_a_session_certificate_reaches_its_own_session_over_tls[same-id-other-item], tests/adapters/test_agent_permission_rules.py::test_an_amp_launch_gets_its_own_settings_file[per-item-session], tests/adapters/test_subprocess.py::test_a_sandbox_client_runs_outside_the_worktree
origin: src/kraft/worker/channel.py §socket_path, src/kraft/worker/channel.py §open, src/kraft/worker/ca.py §session_dir, src/kraft/adapters/agent.py §run_agent_task

## REQ an-older-session-keeps-its-names
WHILE a session minted before per-item ids runs, the system SHALL name its containers, volume, certificate and socket folder as it did when it started.
enforced-by: tests/worker/backends/test_docker_names.py::test_a_session_s_docker_names_carry_its_item[container], tests/worker/backends/test_docker_names.py::test_a_launch_runs_and_joins_the_names_its_session_has[older-session], tests/worker/backends/test_docker_relay.py::test_sweep_keeps_a_live_sessions_relays_volume_and_certificate[older-session], tests/worker/test_channel.py::test_each_session_listens_in_a_socket_folder_of_its_own[older-session], tests/worker/test_channel.py::test_a_session_certificate_reaches_its_own_session_over_tls[older-session], tests/worker/test_reattach_oom.py::test_an_adopted_session_is_found_under_the_name_it_started_with[older-session], tests/adapters/test_agent_permission_rules.py::test_an_amp_launch_gets_its_own_settings_file[older-session]
origin: src/kraft/ids.py §qualified, src/kraft/worker/channel.py §socket_path

## REQ a-summary-carries-its-own-item-s-session
WHEN the system lists a work item's documents, it SHALL give each session summary the run of the session that wrote it, found by that session's work item and session id.
enforced-by: tests/index/test_service_session_links.py::test_a_summary_is_listed_once_with_its_own_item_s_session, frontend/src/ng/item/panes/TaskPane.test.tsx::task pane › lists only this attempt's documents under the result
origin: src/kraft/index/service.py §ingest_session_summary, src/kraft/index/service.py §_session_runs

## REQ a-summary-linked-before-the-upgrade-is-found-by-id
WHILE a session summary's index link records no work item for its session, the system SHALL find that session by its id alone, and SHALL give the summary no run when more than one session has that id.
enforced-by: tests/index/test_service_session_links.py::test_a_summary_linked_before_the_upgrade_keeps_its_run[pre-upgrade-id], tests/index/test_service_session_links.py::test_a_summary_linked_before_the_upgrade_keeps_its_run[an-id-two-items-share], tests/index/test_db.py::test_v3_index_migrates_forward_keeping_its_links_with_no_session_item
origin: src/kraft/index/service.py §_session_runs, src/kraft/index/db.py §_MIGRATIONS
