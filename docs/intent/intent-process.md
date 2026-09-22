# Intent: the intent-driven process

How a repository's intent tree reaches the agents that work in it. The format
itself is in `docs/intent/README.md`.

## REQ intent-dir-stays-inside-the-repo
IF a repository entry's `intent_dir` is absolute or climbs out of the repository, THEN the system SHALL refuse to load that entry.
enforced-by: tests/test_config_repos.py::test_load_repos_rejects_an_entry[an-absolute-intent-dir], tests/test_config_repos.py::test_load_repos_rejects_an_entry[an-intent-dir-escaping-the-repo]
origin: src/kraft/config.py §RepoEntry._intent_dir_inside_repo

## REQ intent-dir-is-named-to-every-agent
WHERE a repository declares `intent_dir`, the system SHALL name that directory in every agent launch's context, after the method and before the project standards.
enforced-by: tests/adapters/test_agent_intent.py::test_the_intent_block_sits_after_the_method_and_before_steering
origin: src/kraft/adapters/agent.py §build_context

## REQ no-intent-dir-names-no-tree
IF a repository declares no `intent_dir`, THEN the system SHALL NOT add an intent-tree block to an agent launch's context.
enforced-by: tests/adapters/test_agent_intent.py::test_no_intent_dir_no_intent_block[no-entry], tests/adapters/test_agent_intent.py::test_no_intent_dir_no_intent_block[null-intent-dir]
origin: src/kraft/adapters/agent.py §build_context

## REQ tree-readme-is-not-a-capability
The intent check SHALL NOT read a tree's `README.md` as a capability file.
enforced-by: tests/test_intent.py::test_main_does_not_parse_the_tree_readme
origin: docs/intent/README.md
