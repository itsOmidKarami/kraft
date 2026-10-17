# Intent: in-place checkout

A repository may run its work items in place, in its own checkout, rather than
each in a git worktree of its own: `worktrees: false` on its `repos.yaml`
entry. An item filed there may say otherwise, and what it is filed as stays
fixed. An in-place item's changes land in the folder the person works in, so
it is never sandboxed.

## REQ in-place-repo-entry-refuses-a-sandbox

IF a repository entry sets `worktrees: false` and a `sandbox`, THEN the system
SHALL refuse the entry.
enforced-by: tests/test_config_repos.py::test_load_repos_rejects_an_entry[in-place-with-a-sandbox], tests/test_config_repos.py::test_load_repos_rejects_an_entry[in-place-with-a-policy-sandbox], tests/api/test_repos_save.py::test_a_save_turning_a_sandboxed_repo_in_place_is_refused
origin: src/kraft/config.py §RepoEntry._in_place_is_never_sandboxed

## REQ in-place-is-frozen-at-intake

WHEN a work item is filed, the system SHALL freeze whether it runs in place
from the item's own flag, else its repository's `worktrees` setting.
enforced-by: tests/api/test_in_place_intake.py::test_intake_freezes_in_place_from_the_item_or_its_repo[repo-default], tests/api/test_in_place_intake.py::test_intake_freezes_in_place_from_the_item_or_its_repo[repo-in-place], tests/api/test_in_place_intake.py::test_intake_freezes_in_place_from_the_item_or_its_repo[item-in-place], tests/api/test_in_place_intake.py::test_intake_freezes_in_place_from_the_item_or_its_repo[item-worktree], tests/templates/test_environment.py::test_an_in_place_target_round_trips_and_an_older_snapshot_reads_as_a_worktree, tests/cli/test_item.py::test_create_sends_whether_the_item_runs_in_place[in-place], tests/cli/test_item.py::test_create_sends_whether_the_item_runs_in_place[worktree], tests/test_mcp.py::test_create_work_item_forwards_the_targets_fields[in-place], tests/api/test_in_place_intake.py::test_every_door_freezes_in_place[trigger], tests/api/test_in_place_intake.py::test_every_door_freezes_in_place[duplicate-of-in-place], tests/api/test_in_place_intake.py::test_every_door_freezes_in_place[duplicate-of-worktree], tests/test_triggers.py::test_a_triggered_item_is_bound_by_its_repositorys_policy[in-place], tests/test_intake_poller.py::test_an_auto_intaken_item_is_bound_by_its_repositorys_policy[in-place]
origin: src/kraft/executor/entry.py §in_place_by

## REQ in-place-refuses-a-sandbox

IF an item would run in place and sandboxed, THEN the system SHALL refuse to
file it and name both settings.
enforced-by: tests/api/test_in_place_intake.py::test_intake_refuses_a_sandboxed_in_place_item[repo-entry], tests/api/test_in_place_intake.py::test_intake_refuses_a_sandboxed_in_place_item[chain], tests/api/test_in_place_intake.py::test_intake_refuses_a_sandboxed_in_place_item[item-policy], tests/executor/test_entry.py::test_a_chain_policy_over_the_ceiling_is_refused_before_any_side_effect[in-place-and-sandboxed]
origin: src/kraft/executor/entry.py §in_place_sandbox_problem

## REQ in-place-refuses-a-workspace

IF a workspace item would run in place, THEN the system SHALL refuse to file
it.
enforced-by: tests/api/test_workspace_intake.py::test_a_workspace_selection_that_cannot_assemble_is_a_422[in-place], tests/api/test_workspace_intake.py::test_a_workspace_selection_that_cannot_assemble_is_a_422[in-an-in-place-root], tests/templates/test_environment.py::test_a_workspace_target_cannot_run_in_place
origin: src/kraft/templates/environment.py §WorkItemTarget._kind_owns_its_fields
