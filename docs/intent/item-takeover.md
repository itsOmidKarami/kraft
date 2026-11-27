# Intent: item takeover

`kraft item takeover` lets a person open a stopped item's Claude Code session
on their own terminal, in the item's worktree, and hand the item back when they
exit. A takeover is a lock on the item's row held by the CLI's own process; it
inserts no worker session, and it never changes the item's status.

## REQ takeover-refuses-what-it-cannot-run-locally

IF an item is not paused or needs_human, runs in a sandbox, ran its step on a
remote worker, uses a harness with no `interactive:` block, has no agent
session on its current node, has several agent tasks side by side in the
step, or has no worktree on disk, THEN the system SHALL refuse the takeover and take no lock.
enforced-by: tests/test_takeover.py::test_prepare_refuses[wrong-status], tests/test_takeover.py::test_prepare_refuses[sandboxed], tests/test_takeover.py::test_prepare_refuses[remote], tests/test_takeover.py::test_prepare_refuses[no-interactive-block], tests/test_takeover.py::test_prepare_refuses[no-session], tests/test_takeover.py::test_prepare_refuses[several-tasks], tests/test_takeover.py::test_prepare_refuses[no-worktree]
origin: src/kraft/takeover.py §prepare

## REQ takeover-refuses-while-something-else-drives-the-worktree

IF a person already has the session open, a worker session or the item's walk
is still stopping, the start queue has claimed the item, or an escalation
turn is running, THEN the system SHALL refuse the takeover and take no lock.
enforced-by: tests/test_takeover.py::test_prepare_refuses[already-live], tests/test_takeover.py::test_prepare_refuses[live-worker], tests/test_takeover.py::test_prepare_refuses[walk-running], tests/test_takeover.py::test_prepare_refuses[start-queue], tests/test_takeover.py::test_prepare_refuses[escalation]
origin: src/kraft/takeover.py §prepare

## REQ takeover-lock-goes-to-one-of-two-concurrent-callers

WHEN two takeovers of one item start at once, the system SHALL give the lock to
exactly one of them.
enforced-by: tests/test_takeover.py::test_only_one_of_two_concurrent_prepares_wins
origin: src/kraft/takeover.py §prepare

## REQ takeover-forks-the-paused-session

WHEN the item's task has a paused session to continue, the system SHALL start
the person's session as a fork of it under an id Kraft minted, and otherwise
SHALL start a fresh session under that id.
enforced-by: tests/test_takeover.py::test_prepare_forks_the_paused_session, tests/test_takeover.py::test_prepare_starts_fresh_when_nothing_is_resumable, tests/test_harnesses_interactive.py::test_interactive_argv_for_claude[forks], tests/test_harnesses_interactive.py::test_interactive_argv_for_claude[fresh]
origin: src/kraft/takeover.py §prepare

## REQ takeover-frees-a-lock-whose-cli-is-gone

IF a lock's CLI process is no longer running when it is next checked, THEN the
system SHALL release the lock before answering.
enforced-by: tests/test_takeover.py::test_live_releases_a_stale_lock, tests/api/test_lifecycle_takeover.py::test_a_stale_lock_is_released_by_the_next_door
origin: src/kraft/takeover.py §live

## REQ takeover-records-the-session-only-when-it-was-written

WHEN a takeover that forked a paused session ends and the session's file
exists, the system SHALL record the new session's id on the paused session's
row, and otherwise SHALL record nothing.
enforced-by: tests/test_takeover.py::test_release_records[forked-and-written], tests/test_takeover.py::test_release_records[forked-but-quit-before-a-turn], tests/test_takeover.py::test_release_records[fresh]
origin: src/kraft/takeover.py §release

## REQ takeover-warns-of-what-the-next-run-would-trip-over

WHEN a takeover ends, the system SHALL warn of uncommitted changes, of a HEAD
off the item's branch, and of a branch that no longer descends from where it
was.
enforced-by: tests/test_takeover.py::test_release_warns[uncommitted], tests/test_takeover.py::test_release_warns[other-branch], tests/test_takeover.py::test_release_warns[rewritten]
origin: src/kraft/takeover.py §release

## REQ takeover-holds-every-door-but-pause

WHILE a person has the item's session open, the system SHALL refuse every verb
that acts on the item except pause.
enforced-by: tests/api/test_lifecycle_takeover.py::test_a_door_refuses_while_a_person_has_the_session[resume], tests/api/test_lifecycle_takeover.py::test_a_door_refuses_while_a_person_has_the_session[retry], tests/api/test_lifecycle_takeover.py::test_a_door_refuses_while_a_person_has_the_session[skip], tests/api/test_lifecycle_takeover.py::test_a_door_refuses_while_a_person_has_the_session[escalate], tests/api/test_lifecycle_takeover.py::test_a_door_refuses_while_a_person_has_the_session[budget_raise], tests/api/test_lifecycle_takeover.py::test_a_door_refuses_while_a_person_has_the_session[rerun_ci], tests/api/test_lifecycle_takeover.py::test_a_door_refuses_while_a_person_has_the_session[cancel], tests/api/test_lifecycle_takeover.py::test_a_door_refuses_while_a_person_has_the_session[complete], tests/api/test_lifecycle_takeover.py::test_a_door_refuses_while_a_person_has_the_session[approve], tests/api/test_lifecycle_takeover.py::test_a_door_refuses_while_a_person_has_the_session[reject], tests/api/test_lifecycle_takeover.py::test_a_door_refuses_while_a_person_has_the_session[abandon], tests/api/test_lifecycle_takeover.py::test_a_door_refuses_while_a_person_has_the_session[unblock], tests/api/test_lifecycle_takeover.py::test_a_patch_of_what_the_session_runs_under_refuses[set-chain], tests/api/test_lifecycle_takeover.py::test_a_patch_of_what_the_session_runs_under_refuses[set-overrides], tests/api/test_lifecycle_takeover.py::test_a_patch_of_what_the_session_runs_under_refuses[set-policy], tests/api/test_lifecycle_takeover.py::test_pause_still_answers_as_it_did
origin: src/kraft/api/deps.py §refuse_if_taken_over

## REQ takeover-keeps-agents-out-of-the-worktree

WHILE a person has the item's session open, the system SHALL NOT dispatch an
automatic escalation into the item.
enforced-by: tests/test_auto_escalate_delay.py::test_tick_dispatches_a_needs_human_item_once_its_delay_has_elapsed[taken-over]
origin: src/kraft/auto_escalate_delay.py §tick

## REQ takeover-is-a-local-persons-door

IF the caller of a takeover route is a worker session or is not on the machine
Kraft runs on, THEN the system SHALL refuse it.
enforced-by: tests/api/test_lifecycle_takeover.py::test_takeover_refuses_a_non_local_peer, tests/api/test_lifecycle_takeover.py::test_takeover_refuses_a_worker_session[post], tests/api/test_lifecycle_takeover.py::test_takeover_refuses_a_worker_session[delete], tests/cli/test_item_takeover.py::test_takeover_refused_inside_a_worker
origin: src/kraft/api/routes/lifecycle.py §_takeover_caller

## REQ takeover-child-runs-without-the-workers-identity

WHEN the CLI runs the agent for a takeover, the system SHALL remove
`KRAFT_WORK_ITEM_ID` and `KRAFT_SESSION_ID` from the agent's environment and
SHALL release the lock when the agent cannot be started.
enforced-by: tests/cli/test_item_takeover.py::test_takeover_strips_worker_env_from_the_child, tests/cli/test_item_takeover.py::test_takeover_releases_when_the_child_fails
origin: src/kraft/cli/item.py §_cmd_takeover

## REQ takeover-resume-continues-the-persons-fork-with-a-note

WHEN a paused task whose row carries a takeover's session resumes, the system
SHALL resume that session and SHALL tell the agent a person worked in it.
enforced-by: tests/executor/test_steer_targets.py::test_a_paused_agent_task_resumes_its_own_session[a-takeover-left-one]
origin: src/kraft/executor/dispatch.py §_resumable_session
