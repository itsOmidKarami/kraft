# Intent: update and restart when Kraft is free

Kraft installs a marked or auto-taken release, and carries out any pending
restart (a dev install, or a settings change), only when nothing is going on
and inside the optional allowed hours. A failed attempt puts the previous
version back. Auto-update and restart-when-free are both off by default.

## REQ pending-update-waits-until-nothing-is-going-on

WHEN a release is marked or a restart is pending, the system SHALL act on it
only while no agent session is running and no work item is `active`, `waiting`,
`queued` or `rate_limited`, and inside the allowed hours.
enforced-by: tests/test_when_free.py::test_free_means_nothing_going_on[nothing], tests/test_when_free.py::test_free_means_nothing_going_on[active], tests/test_when_free.py::test_free_means_nothing_going_on[waiting], tests/test_when_free.py::test_free_means_nothing_going_on[queued], tests/test_when_free.py::test_free_means_nothing_going_on[rate_limited], tests/test_when_free.py::test_free_means_nothing_going_on[needs_human-at-gate], tests/test_when_free.py::test_free_means_nothing_going_on[a-running-session-on-a-needs_human-item], tests/test_when_free.py::test_outside_the_allowed_hours_nothing_happens, tests/test_config_models.py::test_update_settings_allowed_hours[crosses-midnight-late], tests/test_config_models.py::test_update_settings_allowed_hours[crosses-midnight-noon]
origin: src/kraft/when_free.py §is_free

## REQ update-installs-nothing-while-work-runs

IF Kraft is no longer free when the update claims the moment, THEN the system
SHALL NOT install anything.
enforced-by: tests/test_when_free.py::test_busy_at_claim_installs_nothing, tests/test_when_free.py::test_becoming_busy_before_the_restart_keeps_the_install_and_waits
origin: src/kraft/when_free.py §claim

## REQ no-new-work-starts-while-an-update-restarts

WHILE an update holds the claim, the system SHALL NOT start a walk or an agent
session, SHALL answer a gate decision with 503 and change nothing, and SHALL
keep a queued request queued.
enforced-by: tests/api/test_when_free_claim.py::test_approve_during_claim, tests/api/test_when_free_claim.py::test_start_under_claim, tests/test_start_queue.py::test_queued_request_kept_while_claimed
origin: src/kraft/when_free.py §claim

## REQ a-failed-update-puts-the-previous-version-back

IF the new install cannot start, or the restarted server does not answer as a
new process of the expected version, THEN the system SHALL reinstall the
previous version, restore the configuration and the databases, start it again
and record the failure without trying that version again.
enforced-by: tests/test_when_free.py::test_a_start_check_that_fails_on_a_release_puts_the_old_one_back, tests/cli/test_admin_restart.py::test_restart_confirm_rolls_back[same-pid-times-out-and-rolls-back], tests/cli/test_admin_restart.py::test_restart_confirm_rolls_back[new-pid-wrong-version-rolls-back], tests/cli/test_admin_restart.py::test_restart_confirm_rolls_back[settings-restart-restores-access-yaml], tests/cli/test_admin_restart.py::test_restart_confirm_rolls_back[dev-restores-config-and-databases-not-the-package], tests/test_when_free.py::test_pending_names_what_and_why[failed-key-not-retried]
origin: src/kraft/cli/admin.py §_restart_confirm

## REQ a-new-server-runs-nothing-until-confirmed

WHILE a restart is unconfirmed, the system SHALL answer requests and SHALL NOT
resume walks or start pollers, until the helper confirms the new process or the
probation ends.
enforced-by: tests/api/test_startup.py::test_leftover_record, tests/api/test_startup.py::test_confirm_ends_it, tests/api/test_startup.py::test_probation_ends_by_itself
origin: src/kraft/api/startup.py §end_probation

## REQ only-a-handed-off-restart-starts-probation

WHEN a server starts on a rollback record whose restart was never handed to the
helper, the system SHALL start its walks and pollers at once and SHALL NOT
record an update as applied.
enforced-by: tests/api/test_startup.py::test_probation[record-not-handed-off-starts-no-probation], tests/api/test_startup.py::test_probation[handed-off-record-starts-probation]
origin: src/kraft/api/startup.py §lifespan

## REQ an-attempt-that-hands-off-nothing-leaves-no-snapshot

IF an attempt does not hand the restart to the helper, THEN the system SHALL
delete the snapshot it took, and SHALL NOT repeat a restart that failed.
enforced-by: tests/test_when_free.py::test_apply_once[went-busy-leaves-no-snapshot], tests/test_when_free.py::test_apply_once[two-busy-attempts-leave-one-snapshot-at-most], tests/test_when_free.py::test_apply_once[restarter-raises-after-install-is-not-retried]
origin: src/kraft/when_free.py §_apply_claimed

## REQ auto-update-takes-only-minor-and-patch-releases

WHERE `auto_update` is on, the system SHALL mark only a release of the
installed major version, and SHALL NOT mark one on a dev install.
enforced-by: tests/test_when_free.py::test_it_never_auto_marks_a_major, tests/test_when_free.py::test_a_dev_install_never_marks, tests/test_update_installs.py::test_latest_in_major_older_major_still_gets_patches
origin: src/kraft/when_free.py §_auto_mark

## REQ no-restart-into-an-older-install

IF the installed version is older than the running one, THEN the system SHALL
NOT restart into it.
enforced-by: tests/test_when_free.py::test_pending_names_what_and_why[older-install-never-restarts]
origin: src/kraft/when_free.py §pending

## REQ unattended-update-asks-no-question

IF an update would ask a question when run by hand, THEN the system SHALL NOT
install it, SHALL drop the mark and SHALL record why.
enforced-by: tests/cli/test_admin_update.py::test_unattended_precheck_refuses_where_the_command_would_ask[dropped-addon], tests/cli/test_admin_update.py::test_unattended_precheck_refuses_where_the_command_would_ask[pre-v1], tests/test_when_free.py::test_a_precheck_that_would_ask_refuses_the_install
origin: src/kraft/cli/admin.py §update_prechecks

## REQ update-and-restart-when-free-are-off-by-default

WHERE `update.yaml` does not exist, the system SHALL NOT install or restart on
its own.
enforced-by: tests/test_config_models.py::test_update_settings_missing_file_turns_nothing_on, tests/test_when_free.py::test_nothing_pending_does_nothing_and_claims_nothing, tests/test_when_free.py::test_pending_names_what_and_why[newer-install-switch-off]
origin: src/kraft/config.py §Update

## REQ a-broken-update-yaml-installs-and-restarts-nothing

WHILE `update.yaml` does not load, the system SHALL NOT install or restart
when free, a marked release and an asked restart included, and SHALL say why.
enforced-by: tests/test_when_free.py::test_pending_names_what_and_why[broken-update-yaml-blocks-a-mark], tests/test_when_free.py::test_pending_names_what_and_why[broken-update-yaml-blocks-an-asked-restart]
origin: src/kraft/when_free.py §pending
