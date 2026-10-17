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
