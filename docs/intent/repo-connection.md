# Repo connection

A repository's presence in `repos.yaml` gates what Kraft will do with it:
whether intake will file work against it, and whether an edit to its entry is
allowed to leave it unable to verify anything.

## REQ intake-requires-a-connected-repo
IF a work item or a trigger is filed against a repo Kraft has not connected, THEN the system SHALL refuse it with a 422 naming `kraft repo connect`.
enforced-by: tests/api/test_intake_connected.py::test_an_unconnected_repo_is_refused_naming_repo_connect[work-items], tests/api/test_intake_connected.py::test_an_unconnected_repo_is_refused_naming_repo_connect[triggers], tests/api/test_intake_connected.py::test_a_connected_repo_is_filed[work-items], tests/api/test_intake_connected.py::test_a_connected_repo_is_filed[triggers]
origin: src/kraft/api/deps.py §connected_or_422 (Kraft-ta8nv)

## REQ cron-trigger-requires-a-connected-repo
IF a `policy.yaml` cron trigger's tick is due and its repo is not connected, THEN the system SHALL skip that trigger with a logged warning naming `kraft repo connect`, and SHALL still fire the other due triggers in the same tick.
enforced-by: tests/test_triggers.py::test_a_trigger_naming_an_unconnected_repo_is_skipped_not_the_whole_tick
origin: src/kraft/triggers.py §tick (Kraft-jzhg2)

## REQ trigger-poller-runs-without-boot-triggers
WHILE the server is running, the system SHALL run the trigger poller even when no trigger existed at boot, so a trigger added afterward by Settings or `kraft admin reload` fires without a restart.
enforced-by: tests/test_triggers.py::test_the_poller_runs_with_no_triggers_at_boot
origin: src/kraft/api/startup.py (Kraft-ygnw6)

## REQ enabling-a-repo-with-no-test-command-is-refused
IF a PATCH to a connected repo's entry sets it enabled and it has neither a test command nor test scopes, THEN the system SHALL refuse it with a 422.
enforced-by: tests/api/test_repos.py::test_enabling_an_entry_with_no_enabled_key_and_no_test_command_is_refused
origin: src/kraft/api/routes/repos.py §_refuse_enable_without_test_command (Kraft-hv4uy)

## REQ clearing-the-last-test-command-of-an-enabled-repo-is-refused
IF a PATCH would clear an enabled repo's only test command, leaving no test command and no test scopes, THEN the system SHALL refuse it with a 422 and leave the stored entry unchanged.
enforced-by: tests/api/test_repos.py::test_clearing_the_last_test_command_of_an_enabled_entry_is_refused
origin: src/kraft/api/routes/repos.py §update_repo (Kraft-hv4uy)

## REQ a-repo-patch-changing-neither-enabled-nor-tests-goes-through
IF a PATCH to a connected repo's entry changes neither whether it is enabled nor its test command, THEN the system SHALL NOT refuse it, even when the entry already has no test command.
enforced-by: tests/api/test_repos.py::test_a_rename_on_an_enabled_entry_with_no_test_command_goes_through
origin: src/kraft/api/routes/repos.py §update_repo (Kraft-hv4uy)
