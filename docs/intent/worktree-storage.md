# Intent: worktree storage

An operator may bound the disk worktrees use in `policy.yaml`. Over the quota
Kraft warns, and over the limit it holds starts that would create a worktree
until a person makes room. Kraft archives nothing for the limit unless
`storage.worktrees.auto_cleanup` is set.

## REQ no-limit-means-no-measuring

WHERE `storage.worktrees.limit` is not set, the system SHALL NOT walk the run
folder on its periodic tick.
enforced-by: tests/test_storage.py::test_tick_without_a_limit_does_not_walk
origin: src/kraft/storage.py §tick

## REQ over-limit-holds-only-a-start-without-a-worktree

WHILE worktree usage is over `storage.worktrees.limit`, the system SHALL queue
a start of an item that has no worktree and SHALL NOT hold a start of an item
that has one.
enforced-by: tests/api/test_lifecycle_queue.py::test_a_new_start_over_the_storage_limit_is_queued_and_says_why, tests/api/test_lifecycle_queue.py::test_an_item_with_a_worktree_is_not_held_by_the_storage_limit
origin: src/kraft/storage.py §holds

## REQ held-start-keeps-its-place

WHILE a queued start is held for storage, the start queue SHALL skip it and
start the queued items behind it that have a worktree.
enforced-by: tests/test_start_queue.py::test_a_start_held_for_storage_keeps_its_place_and_lets_a_worktree_holder_past
origin: src/kraft/start_queue.py §tick

## REQ clean-up-releases-held-starts-without-a-walk

WHEN an item is archived, the system SHALL take its bytes off the cached
measurement without walking the run folder.
enforced-by: tests/test_archive_poller.py::test_archiving_takes_the_items_bytes_off_the_storage_cache
origin: src/kraft/api/routes/lifecycle.py §_archive_one

## REQ over-limit-degrades-health

WHILE worktree usage is over `storage.worktrees.limit`, the system SHALL report
`/health` as `degraded`, and SHALL NOT while it is only over the quota.
enforced-by: tests/api/test_auth.py::test_health_reports_storage_and_degrades_only_when_held[ok], tests/api/test_auth.py::test_health_reports_storage_and_degrades_only_when_held[over-quota], tests/api/test_auth.py::test_health_reports_storage_and_degrades_only_when_held[held]
origin: src/kraft/api/routes/auth.py §health

## REQ bare-number-size-is-refused

IF a storage size has no unit, THEN the system SHALL refuse the policy and name
the unit to add.
enforced-by: tests/test_policy_storage.py::test_size_bytes_refuses_anything_else_and_names_the_fix[bare-int], tests/test_policy_storage.py::test_size_bytes_refuses_anything_else_and_names_the_fix[bare-str]
origin: src/kraft/policy.py §size_bytes

## REQ auto-cleanup-is-off-without-its-key

WHILE worktree usage is over `storage.worktrees.limit` and
`storage.worktrees.auto_cleanup` is not set, the system SHALL NOT archive any
item.
enforced-by: tests/test_storage.py::test_tick_without_auto_cleanup_archives_nothing
origin: src/kraft/storage.py §tick

## REQ auto-cleanup-archives-oldest-first-down-to-quota

WHILE worktree usage is over `storage.worktrees.limit` and `auto_cleanup` is
set, the system SHALL archive completed and abandoned items, oldest first,
until usage is at or under the quota.
enforced-by: tests/test_storage.py::test_tick_over_limit_archives_oldest_first_down_to_quota, tests/test_storage.py::test_tick_between_quota_and_limit_archives_nothing
origin: src/kraft/storage.py §tick

## REQ auto-cleanup-spares-recent-items

The system SHALL NOT archive an item for the limit that ended less than
`auto_cleanup.min_age` ago.
enforced-by: tests/test_storage.py::test_tick_never_takes_an_item_newer_than_min_age
origin: src/kraft/storage.py §tick

## REQ auto-cleanup-stops-when-it-frees-nothing

IF three archives in a row leave their worktrees in place, THEN the system
SHALL end that automatic clean-up pass.
enforced-by: tests/test_storage.py::test_tick_stops_after_three_archives_that_free_nothing
origin: src/kraft/storage.py §tick

## REQ one-archive-per-item

WHEN two callers archive the same item at once, the system SHALL archive it
once and record one archive event.
enforced-by: tests/test_archive_poller.py::test_an_item_is_archived_once_when_two_callers_race
origin: src/kraft/api/routes/lifecycle.py §_archive_one

## REQ auto-cleanup-age-needs-hours-or-days

IF `auto_cleanup.min_age` is not a whole number with the unit `h` or `d`, THEN
the system SHALL refuse the policy and name `24h`.
enforced-by: tests/test_policy_storage.py::test_storage_worktrees_refuses[auto-cleanup-age-without-unit], tests/test_policy_storage.py::test_storage_worktrees_refuses[auto-cleanup-weeks]
origin: src/kraft/policy.py §age_seconds

## REQ auto-cleanup-needs-a-limit

IF `storage.worktrees.auto_cleanup` is set without `limit`, THEN the system
SHALL refuse the policy.
enforced-by: tests/test_policy_storage.py::test_storage_worktrees_refuses[auto-cleanup-without-limit]
origin: src/kraft/policy.py §StorageWorktreesInput
