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
