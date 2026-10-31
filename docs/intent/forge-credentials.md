# Forge credentials

How Kraft reads, proves and protects the token of a `client: api` forge
account: where the token comes from, which origin it is sent to, and the
places it must never appear. Until the API backends ship, an item on such an
account stops at its first forge node rather than run the forge's CLI.

## REQ no-token-in-kraft-written-text
The system SHALL replace every resolved forge token and its Basic form with `<redacted>` in a forge error, a task log Kraft writes and an event payload.
enforced-by: tests/test_redact.py::test_a_registered_token_never_reaches_a_sink[plain-forge-error], tests/test_redact.py::test_a_registered_token_never_reaches_a_sink[plain-git-stderr], tests/test_redact.py::test_a_registered_token_never_reaches_a_sink[plain-node-log], tests/test_redact.py::test_a_registered_token_never_reaches_a_sink[plain-event-payload], tests/adapters/forge/test_credentials.py::test_a_read_token_and_its_basic_form_are_registered, tests/adapters/forge/test_probe.py::test_the_probe_never_puts_a_token_in_its_result, tests/adapters/forge/test_http.py::test_an_error_body_echoing_the_token_is_redacted
origin: src/kraft/redact.py

## REQ env-source-never-names-a-kraft-or-agent-variable
IF an `env` source names a variable Kraft or its agents use, THEN the system SHALL refuse forges.yaml naming the key, and SHALL NOT remove that variable from its environment.
enforced-by: tests/test_forges.py::test_a_bad_account_is_refused_naming_the_file_and_key[env-names-anthropic-key], tests/test_forges.py::test_a_bad_account_is_refused_naming_the_file_and_key[env-names-kraft-var], tests/test_forges.py::test_a_bad_account_is_refused_naming_the_file_and_key[env-names-git-var], tests/test_forges.py::test_an_env_name_kraft_needs_is_never_a_source[PATH-True], tests/test_forges.py::test_an_env_name_kraft_needs_is_never_a_source[GIT_CONFIG_COUNT-True], tests/test_forges.py::test_an_env_name_kraft_needs_is_never_a_source[ANTHROPIC_API_KEY-True], tests/adapters/forge/test_credentials.py::test_a_refused_name_in_an_unreadable_file_is_left_alone
origin: src/kraft/forges.py §env_name_problem

## REQ env-token-leaves-every-child-environment
WHEN forges.yaml names an `env` source, the system SHALL remove that variable from the server's environment before it builds the local worker link, also when the file does not load.
enforced-by: tests/adapters/forge/test_credentials.py::test_an_env_source_leaves_the_server_environment[loaded-file], tests/adapters/forge/test_credentials.py::test_an_env_source_leaves_the_server_environment[unreadable-file], tests/adapters/forge/test_credentials.py::test_an_env_source_leaves_the_server_environment[invalid-file], tests/adapters/forge/test_credentials.py::test_an_env_source_leaves_the_server_environment[quoted-name], tests/adapters/forge/test_credentials.py::test_an_env_source_leaves_the_server_environment[non-utf8-file], tests/adapters/forge/test_credentials.py::test_an_env_source_leaves_the_server_environment[quoted-key], tests/adapters/forge/test_credentials.py::test_an_env_source_leaves_the_server_environment[space-before-colon], tests/api/test_startup.py::test_the_server_drops_an_env_token_before_it_builds_the_local_link
origin: src/kraft/adapters/forge/credentials.py §strip_env_sources

## REQ restart-keeps-env-tokens
WHEN the server starts its own restart, the system SHALL hand the env tokens it took for the accounts forges.yaml still names to the restarting process's environment, and SHALL NOT put them in its argv.
enforced-by: tests/cli/test_admin_restart.py::test_a_restart_the_server_spawns_keeps_its_env_tokens[named-account-keeps-its-token], tests/cli/test_admin_restart.py::test_a_restart_the_server_spawns_keeps_its_env_tokens[removed-account-token-not-passed], tests/cli/test_admin_restart.py::test_a_restart_the_server_spawns_keeps_its_env_tokens[switched-to-file-token-not-passed]
origin: src/kraft/apply.py §spawn_restart

## REQ env-passthrough-of-a-token-source-is-refused
IF a repo's `env_passthrough` names the variable an `env` source reads, THEN the system SHALL refuse the forges.yaml reload or the config draft that adds it, and SHALL NOT pass the variable to a worker.
enforced-by: tests/test_forges.py::test_account_problems_names_each[env-passthrough-names-a-token-source], tests/api/test_admin.py::test_a_forges_yaml_that_leaves_a_repo_without_its_account_is_refused_on_reload[new-env-source-a-repo-passes-through-is-refused], tests/api/test_config_check.py::test_a_save_is_refused_only_for_a_forge_account_problem_it_adds[repos-new-passthrough-of-a-token-source-refused], tests/adapters/forge/test_credentials.py::test_an_env_source_leaves_the_server_environment[passthrough-names-it]
origin: src/kraft/forges.py §account_problems

## REQ credential-lookup-never-prompts
The system SHALL run `git credential fill` and a `command` source outside any repository, in a new session, with prompts and askpass off and a timeout that ends its whole session, and SHALL NOT run `git credential approve` or `reject`.
enforced-by: tests/adapters/forge/test_credentials.py::test_git_credential_fill_runs_without_a_prompt_from_a_neutral_directory, tests/adapters/forge/test_credentials.py::test_a_hung_lookup_times_out_naming_what_may_wait[darwin], tests/adapters/forge/test_credentials.py::test_a_hung_lookup_times_out_naming_what_may_wait[linux], tests/adapters/forge/test_credentials.py::test_a_hung_lookup_times_out_naming_what_may_wait[descendant-left-the-session], tests/adapters/forge/test_credentials.py::test_a_command_that_prints_endlessly_is_refused_fast[command-stdout-endless], tests/adapters/forge/test_credentials.py::test_a_command_that_prints_endlessly_is_refused_fast[command-stderr-endless]
origin: src/kraft/adapters/forge/credentials.py §neutral_run

## REQ source-output-never-in-an-error
IF a token source fails, THEN the system SHALL say why in fixed words and SHALL NOT include the source's output.
enforced-by: tests/adapters/forge/test_credentials.py::test_a_source_that_finds_nothing_says_why_without_its_output[git-exits-128], tests/adapters/forge/test_credentials.py::test_a_source_that_finds_nothing_says_why_without_its_output[command-exits-1], tests/adapters/forge/test_credentials.py::test_a_source_that_finds_nothing_says_why_without_its_output[command-two-lines], tests/adapters/forge/test_credentials.py::test_a_token_with_whitespace_inside_is_refused_without_quoting_it[git-credential], tests/adapters/forge/test_credentials.py::test_a_token_with_whitespace_inside_is_refused_without_quoting_it[command], tests/adapters/forge/test_credentials.py::test_a_token_with_whitespace_inside_is_refused_without_quoting_it[env], tests/adapters/forge/test_credentials.py::test_a_token_with_whitespace_inside_is_refused_without_quoting_it[file]
origin: src/kraft/adapters/forge/credentials.py §NoToken

## REQ file-source-is-private-and-out-of-worker-reach
IF a `file` source is a symlink, readable by group or others, another user's, or inside a connected repo, the worktrees directory, a `sandbox.expose` root or a system directory, THEN the system SHALL refuse to read it.
enforced-by: tests/adapters/forge/test_credentials.py::test_a_file_that_is_not_safe_to_read_is_refused[file-symlink], tests/adapters/forge/test_credentials.py::test_a_file_that_is_not_safe_to_read_is_refused[file-group-readable], tests/adapters/forge/test_credentials.py::test_a_file_that_is_not_safe_to_read_is_refused[file-other-owner], tests/adapters/forge/test_credentials.py::test_a_file_that_is_not_safe_to_read_is_refused[file-inside-repo], tests/adapters/forge/test_credentials.py::test_a_file_that_is_not_safe_to_read_is_refused[file-inside-worktrees], tests/adapters/forge/test_credentials.py::test_a_file_that_is_not_safe_to_read_is_refused[file-inside-expose], tests/adapters/forge/test_credentials.py::test_a_file_that_is_not_safe_to_read_is_refused[file-in-etc], tests/adapters/forge/test_credentials.py::test_a_file_that_is_not_safe_to_read_is_refused[file-in-a-system-dir]
origin: src/kraft/adapters/forge/credentials.py §read_file

## REQ sandbox-never-shows-a-token-file
The system SHALL refuse to launch a sandbox that would show `$KRAFT_HOME/secrets` or any configured `file` source, naming the path, also while forges.yaml does not load.
enforced-by: tests/worker/test_host_view.py::test_a_token_file_is_never_shown_to_a_sandbox[secrets-dir], tests/worker/test_host_view.py::test_a_token_file_is_never_shown_to_a_sandbox[file-source-elsewhere], tests/worker/test_host_view.py::test_a_token_file_is_never_shown_to_a_sandbox[file-source-in-a-broken-forges-yaml], tests/worker/backends/test_bwrap.py::test_an_expose_holding_a_token_file_is_refused_naming_it, tests/worker/test_backends.py::test_a_refused_wrap_stops_the_session_as_config_error[no-network], tests/api/test_startup.py::test_the_server_keeps_a_token_file_from_every_sandbox_from_boot, tests/adapters/forge/test_credentials.py::test_a_broken_file_still_names_its_token_files[plain], tests/adapters/forge/test_credentials.py::test_a_broken_file_still_names_its_token_files[non-utf8-file], tests/adapters/forge/test_credentials.py::test_a_broken_file_still_names_its_token_files[quoted-key], tests/adapters/forge/test_credentials.py::test_a_broken_file_still_names_its_token_files[space-before-colon]
origin: src/kraft/worker/host_view.py §_protected

## REQ token-goes-only-to-its-accounts-origin
The system SHALL send a token only to its account's API origin, SHALL follow a redirect only within that origin, and SHALL refuse an absolute path, another port or `http://`.
enforced-by: tests/adapters/forge/test_http.py::test_a_redirect_is_followed_only_within_the_origin[same-origin-followed], tests/adapters/forge/test_http.py::test_a_redirect_is_followed_only_within_the_origin[other-host-refused], tests/adapters/forge/test_http.py::test_a_redirect_is_followed_only_within_the_origin[other-port-refused], tests/adapters/forge/test_http.py::test_a_redirect_is_followed_only_within_the_origin[http-downgrade-refused], tests/adapters/forge/test_http.py::test_a_redirect_is_followed_only_within_the_origin[on-a-post-refused], tests/adapters/forge/test_http.py::test_a_redirect_is_followed_only_within_the_origin[more-than-five-refused], tests/adapters/forge/test_http.py::test_an_absolute_or_protocol_relative_path_is_refused[absolute-url], tests/adapters/forge/test_http.py::test_an_absolute_or_protocol_relative_path_is_refused[protocol-relative], tests/adapters/forge/test_http.py::test_a_netrc_for_the_host_never_replaces_the_bearer, tests/test_forges.py::test_a_bad_account_is_refused_naming_the_file_and_key[api-url-other-port]
origin: src/kraft/adapters/forge/http.py §HttpTransport

## REQ tls-failure-is-named-never-bypassed
IF the forge's certificate does not verify, THEN the system SHALL fail the call as `tls` naming `ca_file` after exactly one request, and every context it builds SHALL require verification and check the host name.
enforced-by: tests/adapters/forge/test_http.py::test_an_error_is_a_structured_forge_error[tls], tests/adapters/forge/test_http.py::test_the_ca_comes_from_ca_file_then_git_then_the_system[ca-file], tests/adapters/forge/test_http.py::test_the_ca_comes_from_ca_file_then_git_then_the_system[git-sslcainfo], tests/adapters/forge/test_http.py::test_the_ca_comes_from_ca_file_then_git_then_the_system[system], tests/adapters/forge/test_http.py::test_the_ca_comes_from_ca_file_then_git_then_the_system[system-empty-adds-certifi], tests/adapters/forge/test_http.py::test_the_ca_comes_from_ca_file_then_git_then_the_system[ca-file-missing-is-tls]
origin: src/kraft/adapters/forge/http.py §ssl_context

## REQ api-account-never-falls-back-to-the-cli
WHILE the API backend for an account's forge is not installed, the system SHALL stop an item on a `client: api` account at its first forge node naming the account, and SHALL NOT run the forge's CLI for it from any route.
enforced-by: tests/adapters/forge/test_run_pins.py::test_an_api_account_stops_at_the_first_forge_node_and_never_runs_the_cli[named-account], tests/adapters/forge/test_run_pins.py::test_an_api_account_stops_at_the_first_forge_node_and_never_runs_the_cli[host-default], tests/adapters/forge/test_run_pins.py::test_an_api_account_stops_at_the_first_forge_node_and_never_runs_the_cli[member-on-an-api-account], tests/adapters/forge/test_run_pins.py::test_an_api_account_stops_at_the_first_forge_node_and_never_runs_the_cli[member-with-no-forge-takes-the-roots-api-pin], tests/api/test_forge_reads.py::test_an_api_account_item_reads_nothing_through_the_cli[before-any-pin], tests/api/test_forge_reads.py::test_an_api_account_item_reads_nothing_through_the_cli[pinned], tests/api/test_forge_reads.py::test_an_api_account_item_reads_nothing_through_the_cli[member-with-no-forge-of-its-own], tests/api/test_mr_labels.py::test_an_api_account_item_labels_nothing_through_the_cli, tests/api/test_lifecycle_cancel.py::test_cancel_closes_nothing_through_the_cli_on_an_api_account, tests/api/test_lifecycle_cancel.py::test_reopen_runs_nothing_through_the_cli_on_an_api_account, tests/api/test_forges.py::test_test_probes_an_api_account_and_never_runs_the_cli
origin: src/kraft/adapters/forge/run.py §refuse_api_account

## REQ unreadable-forges-yaml-stops-an-unpinned-mount
WHILE forges.yaml does not load, the system SHALL stop an item at a forge node for a mount not yet pinned.
enforced-by: tests/adapters/forge/test_run_pins.py::test_a_forges_yaml_that_does_not_load_never_unpins_or_stops_a_pinned_item[unreadable-file-stops-unpinned-host-default], tests/adapters/forge/test_run_pins.py::test_a_forges_yaml_that_does_not_load_never_unpins_or_stops_a_pinned_item[restart-unreadable-named-stops], tests/api/test_forge_reads.py::test_an_api_account_item_reads_nothing_through_the_cli[unreadable-file-before-any-pin]
origin: src/kraft/adapters/forge/run.py

## REQ a-null-pin-keeps-the-cli
WHILE an item's pin names no account, the system SHALL keep running it with the CLI after an API host default appears.
enforced-by: tests/adapters/forge/test_run_pins.py::test_the_first_forge_node_pins_and_a_broken_pin_stops_the_item[null-pin-keeps-the-cli]
origin: src/kraft/adapters/forge/run.py

## REQ client-change-stops-a-pinned-item
WHEN a pinned account's `client` changes, the system SHALL stop the item naming the account and the change.
enforced-by: tests/adapters/forge/test_run_pins.py::test_the_first_forge_node_pins_and_a_broken_pin_stops_the_item[client-changed-to-api-stops], tests/adapters/forge/test_run_pins.py::test_the_first_forge_node_pins_and_a_broken_pin_stops_the_item[client-changed-to-cli-stops]
origin: src/kraft/adapters/forge/run.py

## REQ health-shows-token-state-not-identity
The system SHALL put only each API account's token state and expiry date on `/health`.
enforced-by: tests/api/test_admin.py::test_health_reports_a_tokens_state_and_never_who_it_is[ok], tests/api/test_admin.py::test_health_reports_a_tokens_state_and_never_who_it_is[refused]
origin: src/kraft/api/routes/auth.py §health

## REQ probe-runs-in-the-server-not-per-poll
The system SHALL probe API credentials in the server at start, at reload, on `kraft admin forge test` and on a timer, and SHALL NOT probe on a `/health` request.
enforced-by: tests/adapters/forge/test_probe.py::test_probes_run_at_start_reload_test_and_timer_never_per_health_poll, tests/adapters/forge/test_probe.py::test_the_timer_probes_again_with_nothing_asking
origin: src/kraft/adapters/forge/probe.py §loop

## REQ service-unit-holds-no-token
The system SHALL NOT write a token source's value into a service unit or plist.
enforced-by: tests/cli/test_service.py::test_service_files_never_carry_an_env_token[systemd], tests/cli/test_service.py::test_service_files_never_carry_an_env_token[launchd]
origin: src/kraft/cli/admin.py

## REQ forge-account-is-a-persons-choice
IF an agent or a worker sets or clears a repo's `forge_account`, THEN the system SHALL refuse it.
enforced-by: tests/api/test_repos_held.py::test_an_agent_does_not_choose_a_repos_forge_account[assistant-connects-naming-one], tests/api/test_repos_held.py::test_an_agent_does_not_choose_a_repos_forge_account[worker-connects-naming-one], tests/api/test_repos_held.py::test_turning_a_repo_on_is_a_persons_call[worker-names-a-forge-account], tests/api/test_repos_held.py::test_turning_a_repo_on_is_a_persons_call[assistant-clears-a-forge-account]
origin: src/kraft/api/routes/repos.py

## REQ forges-yaml-refusal-never-quotes-a-value
IF forges.yaml does not load, THEN the system SHALL name the key and SHALL NOT quote its value or chain the validation error.
enforced-by: tests/test_forges.py::test_a_bad_account_is_refused_naming_the_file_and_key[token-as-a-string]
origin: src/kraft/forges.py §ForgesFile.load

## REQ pasted-token-is-never-echoed
WHEN a person pastes a token, the system SHALL write it to a 0600 file in a 0700 directory and SHALL NOT return it in any answer, a refusal included.
enforced-by: tests/api/test_forges.py::test_a_pasted_token_is_written_private_and_never_echoed[missing-kind], tests/api/test_forges.py::test_a_pasted_token_is_written_private_and_never_echoed[not-json], tests/api/test_forges.py::test_a_pasted_token_is_written_private_and_never_echoed[value-not-a-string], tests/api/test_forges.py::test_a_pasted_token_is_written_private_and_never_echoed[kind-is-the-value], tests/cli/test_repos_connect_api.py::test_a_token_on_stdin_reaches_only_set_forge_token
origin: src/kraft/api/routes/forges.py

## REQ command-source-is-set-on-the-servers-machine
IF an HTTP request sets a `command` source, THEN the system SHALL refuse it.
enforced-by: tests/api/test_forges.py::test_a_refused_add_writes_nothing[command-source-403], tests/api/test_forges.py::test_edit_changes_only_the_fields_given[command-403]
origin: src/kraft/api/routes/forges.py

## REQ routes-protect-a-new-source-at-once
WHEN a route saves a new `env` or `file` token source, the system SHALL remove the variable from its environment or refuse the file to a sandbox before the answer returns.
enforced-by: tests/api/test_forges.py::test_a_new_source_is_protected_at_once[add-env], tests/api/test_forges.py::test_a_new_source_is_protected_at_once[add-file], tests/api/test_forges.py::test_a_new_source_is_protected_at_once[edit-file], tests/api/test_forges.py::test_a_new_source_is_protected_at_once[edit-env]
origin: src/kraft/api/routes/forges.py

## REQ connect-without-api-flags-is-unchanged
WHILE a person passes no API flag and no matching API account exists, the system SHALL connect a repo with no new prompt and no forge account call before the repo is connected.
enforced-by: tests/cli/test_repos_connect.py::test_an_existing_user_sees_no_change[github-https-terminal], tests/cli/test_repos_connect.py::test_an_existing_user_sees_no_change[github-https-no-terminal], tests/cli/test_repos_connect.py::test_an_existing_user_sees_no_change[github-ssh-terminal], tests/cli/test_repos_connect.py::test_an_existing_user_sees_no_change[github-ssh-no-terminal], tests/cli/test_repos_connect.py::test_an_existing_user_sees_no_change[no-origin-terminal], tests/cli/test_repos_connect.py::test_an_existing_user_sees_no_change[no-origin-no-terminal]
origin: src/kraft/cli/repo.py

## REQ a-token-is-never-a-variable-name
IF a name given for an `env` source starts with a known token prefix, THEN the system SHALL refuse it in fixed words that do not repeat the name.
enforced-by: tests/test_forges.py::test_a_bad_account_is_refused_naming_the_file_and_key[env-holds-a-token], tests/cli/test_repos_connect_api.py::test_an_api_account_asks_where_the_token_comes_from_only_when_git_has_none[env-refuses-a-pasted-token]
origin: src/kraft/forges.py

## REQ sso-stop-names-the-authorize-link
WHEN a forge refuses a token and names an `https://` single sign-on address with no whitespace or backtick, the system SHALL put that address in the stop's facts and reason, and SHALL drop any other address.
enforced-by: tests/executor/test_walk_forge_cause.py::test_a_refused_token_stop_carries_the_sso_address_only_when_it_is_safe[fix-loop-sso-on-a-cli-account], tests/executor/test_walk_forge_cause.py::test_a_refused_token_stop_carries_the_sso_address_only_when_it_is_safe[no-loop-sso-on-a-cli-account], tests/executor/test_walk_forge_cause.py::test_a_refused_token_stop_carries_the_sso_address_only_when_it_is_safe[fix-loop-backtick-dropped], tests/executor/test_walk_forge_cause.py::test_a_refused_token_stop_carries_the_sso_address_only_when_it_is_safe[no-loop-non-https-dropped]
origin: src/kraft/executor/walk.py
