# Intent: test groups

A test scope's wait for a slot (`concurrency_group`, a full machine or a full local worker).

## REQ waiting-scope-stands-down-when-item-is-paused
WHILE a test scope waits for a slot, WHEN its work item becomes paused, the system SHALL stop the wait, start no session for the scope, and end the scope `paused`.
enforced-by: tests/remote/test_local_link.py::test_a_waiting_take_stands_down_when_abandoned[capacity-full], tests/remote/test_local_link.py::test_a_waiting_take_stands_down_when_abandoned[group-held], tests/executor/test_targeted_scopes.py::test_a_scope_waiting_for_its_group_does_not_launch_once_the_item_is_paused[paused]
origin: src/kraft/executor/dispatch.py

## REQ abandoned-wait-holds-nothing
IF a scope's wait is abandoned, THEN the system SHALL give back the slot and the concurrency group it took or reserved.
enforced-by: tests/remote/test_launch.py::test_an_abandoned_placement_gives_back_what_it_held, tests/remote/test_hub.py::test_an_abandoned_claim_holds_nothing, tests/adapters/test_subprocess_local.py::test_a_session_waiting_for_the_local_worker_does_not_start_once_abandoned
origin: src/kraft/remote/launch.py
