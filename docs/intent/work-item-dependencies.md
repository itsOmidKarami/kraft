# Intent: work item dependencies

A work item can name the items it comes after. Started while one is unfinished
it is `blocked`, and Kraft starts it when they have all completed.

## REQ start-with-unfinished-dependency-is-blocked

WHEN a work item that comes after an unfinished work item is started, the
system SHALL hold it as `blocked` ahead of the capacity check instead of
starting or queueing it.
enforced-by: tests/api/test_lifecycle_blocked.py::test_a_start_with_an_unfinished_dependency_is_blocked[resume], tests/api/test_lifecycle_blocked.py::test_a_start_with_an_unfinished_dependency_is_blocked[retry]
origin: src/kraft/api/routes/lifecycle.py §blocked_answer

## REQ dependency-is-met-only-when-completed

A blocked work item SHALL be released only when every item it comes after has
status `completed`, and no other status of that item SHALL release it.
enforced-by: tests/store/test_work_items_blocked.py::test_a_blocked_item_is_released_only_when_what_it_comes_after_completed[paused], tests/store/test_work_items_blocked.py::test_a_blocked_item_is_released_only_when_what_it_comes_after_completed[active], tests/store/test_work_items_blocked.py::test_a_blocked_item_is_released_only_when_what_it_comes_after_completed[waiting], tests/store/test_work_items_blocked.py::test_a_blocked_item_is_released_only_when_what_it_comes_after_completed[rate_limited], tests/store/test_work_items_blocked.py::test_a_blocked_item_is_released_only_when_what_it_comes_after_completed[needs_human]
origin: src/kraft/store/work_items.py §release_blocked

## REQ released-item-goes-through-the-queue

WHEN a blocked work item is released, the system SHALL move it to `queued` and
SHALL start it only when a slot is free.
enforced-by: tests/test_start_queue.py::test_a_released_item_waits_in_the_queue_when_the_board_is_full, tests/test_start_queue.py::test_a_blocked_item_starts_once_what_it_comes_after_completes
origin: src/kraft/start_queue.py

## REQ abandoned-dependency-is-never-guessed-away

IF an item a blocked work item comes after is abandoned, THEN the system SHALL
put the blocked item back where it came from and record the reason, and SHALL
NOT start it.
enforced-by: tests/test_start_queue.py::test_a_blocked_item_is_put_back_when_what_it_comes_after_is_abandoned, tests/api/test_lifecycle_blocked.py::test_a_start_after_an_abandoned_item_is_refused
origin: src/kraft/store/work_items.py §release_blocked

## REQ unblock-rejects-a-name-that-is-not-a-dependency

IF `unblock` names a dependency the work item does not have, THEN the system
SHALL refuse with 409 and drop nothing.
enforced-by: tests/api/test_lifecycle_blocked.py::test_unblock_refuses_an_id_that_is_not_a_dependency
origin: src/kraft/api/routes/lifecycle.py §unblock_work_item
