# Intent: Kraft plugins and collections

Teams share chains, library components, skills and agent profiles as Kraft
plugins listed in a collection. An instance pins each installed plugin to a
commit, changes it only through a reviewed update, and never lets a plugin
change what a run may do. `enforced-by:` names the test that pins each
requirement.

## REQ plugin-content-is-addressed-by-namespace

The system SHALL address every component, chain, skill and agent profile a Kraft plugin provides, from outside that plugin, as `<namespace>:<name>`, where the namespace is the plugin's name or its install alias.
enforced-by: tests/templates/test_library_plugins.py::test_plugin_layer_resolution[local-extends-plugin], tests/test_skill.py::test_plugin_skill_references[loaded-plugin-is-read], tests/templates/test_environment.py::test_plugin_profiles[qualified]
origin: src/kraft/templates/library.py §TemplateLibrary.from_yaml_dir

## REQ an-alias-becomes-the-namespace

WHERE a plugin is installed under an alias, the system SHALL address its components, chains, skills and agent profiles by the alias, including the references the plugin makes to its own name.
enforced-by: tests/templates/test_library_plugins.py::test_plugin_layer_resolution[alias-from-outside], tests/templates/test_library_plugins.py::test_plugin_layer_resolution[alias-own-qualified-ref]
origin: src/kraft/plugins/load.py §qualify

## REQ a-bare-local-name-is-local

WHEN local configuration references a component, chain, steering profile or agent profile by a bare name, the system SHALL NOT resolve it to a plugin's declaration.
enforced-by: tests/templates/test_library_plugins.py::test_plugin_layer_resolution[bare-local-is-not-a-plugin-component]
origin: src/kraft/templates/library.py §TemplateLibrary

## REQ a-local-declaration-is-bare

IF a local chain id, `library.yaml` key or `harnesses.yaml` profile id contains `:`, THEN the system SHALL refuse it.
enforced-by: tests/templates/test_library_plugins.py::test_a_local_declaration_with_a_colon_is_refused[chain-id], tests/templates/test_library_plugins.py::test_a_local_declaration_with_a_colon_is_refused[library-key], tests/templates/test_library_plugins.py::test_a_local_declaration_with_a_colon_is_refused[profile-id]
origin: src/kraft/templates/library.py §_add_chain

## REQ plugin-bare-names-resolve-within-the-plugin

WHEN a plugin's component references a bare name in `extends`, `steering` or `skill`, the system SHALL resolve it to that plugin's own declaration.
enforced-by: tests/templates/test_library_plugins.py::test_plugin_layer_resolution[plugin-bare-extends], tests/templates/test_library_plugins.py::test_plugin_layer_resolution[plugin-bare-steering], tests/templates/test_library_plugins.py::test_plugin_layer_resolution[plugin-bare-skill]
origin: src/kraft/plugins/load.py §qualify

## REQ plugin-harness-and-bare-profile-are-the-instances

WHEN a plugin's task names a harness or a bare agent profile, the system SHALL resolve it against the instance's `harnesses.yaml`.
enforced-by: tests/templates/test_library_plugins.py::test_plugin_layer_resolution[plugin-bare-profile-is-instance]
origin: src/kraft/plugins/load.py §qualify

## REQ plugin-cannot-define-harness-profiles

IF a plugin's profiles file contains a `harnesses` section, THEN the system SHALL refuse the plugin.
enforced-by: tests/plugins/test_manifest.py::test_a_plugin_is_refused[harnesses-section]
origin: src/kraft/plugins/manifest.py §check_plugin

## REQ extraction-takes-only-regular-layout-files

WHEN the system extracts a plugin, it SHALL extract only the fixed layout's files, and SHALL refuse the plugin IF one of them is a symlink, a submodule, an LFS pointer, larger than its limit, or carries a control, bidi or format character.
enforced-by: tests/plugins/test_fetch.py::test_extraction_refuses[symlink], tests/plugins/test_fetch.py::test_extraction_refuses[submodule], tests/plugins/test_fetch.py::test_extraction_refuses[lfs-pointer], tests/plugins/test_fetch.py::test_extraction_refuses[oversized-yaml], tests/plugins/test_fetch.py::test_extraction_refuses[oversized-plugin], tests/plugins/test_fetch.py::test_extraction_refuses[too-many-files], tests/plugins/test_fetch.py::test_extraction_refuses[format-character], tests/plugins/test_fetch.py::test_only_the_layout_is_extracted
origin: src/kraft/plugins/fetch.py §extract_git

## REQ fetch-runs-no-repository-code

WHEN the system fetches a collection, it SHALL NOT check out a working tree or run a hook.
enforced-by: tests/plugins/test_fetch.py::test_fetch_never_checks_out_or_runs_hooks
origin: src/kraft/plugins/fetch.py §fetch

## REQ namespace-kraft-is-reserved

IF a plugin is named `kraft` or installed under the alias `kraft`, THEN the system SHALL refuse it.
enforced-by: tests/plugins/test_manifest.py::test_a_plugin_is_refused[kraft-name], tests/plugins/test_config.py::test_plugins_yaml_is_refused[kraft-alias]
origin: src/kraft/plugins/config.py §PluginsConfig

## REQ plugin-namespaces-are-unique

IF a plugin's namespace equals another installed plugin's namespace or a `repos.yaml` entry's `id`, THEN the system SHALL refuse the install.
enforced-by: tests/plugins/test_config.py::test_plugins_yaml_is_refused[duplicate-namespace]
origin: src/kraft/plugins/config.py §PluginsConfig

## REQ a-plugin-runs-on-one-kraft-major

IF the running Kraft's release is below a plugin's `requires.kraft` version or in a different major, THEN the system SHALL refuse the plugin at validate, install and update, and SHALL leave it out at load.
enforced-by: tests/plugins/test_manifest.py::test_kraft_compatibility[below-minimum], tests/plugins/test_manifest.py::test_kraft_compatibility[next-major], tests/plugins/test_manifest.py::test_kraft_compatibility[earlier-major], tests/plugins/test_manifest.py::test_kraft_compatibility[release-candidate], tests/plugins/test_load.py::test_a_plugin_for_another_major_is_left_out
origin: src/kraft/plugins/manifest.py §kraft_compatible

## REQ unknown-manifest-keys-are-ignored

The system SHALL ignore a top-level `collection.json` or `plugin.json` key it does not read, and SHALL refuse an unknown key inside `requires`.
enforced-by: tests/plugins/test_manifest.py::test_unknown_keys_are_ignored[collection], tests/plugins/test_manifest.py::test_unknown_keys_are_ignored[plugin], tests/plugins/test_manifest.py::test_unknown_keys_are_ignored[claude-code-keys], tests/plugins/test_manifest.py::test_a_plugin_is_refused[unknown-requires-key]
origin: src/kraft/plugins/manifest.py §PluginManifest

## REQ validate-warns-about-unknown-keys

WHEN `validate` reads a manifest key Kraft does not read, the system SHALL list it as a warning.
enforced-by: tests/cli/test_plugin.py::test_validate[unknown-key-warning]
origin: src/kraft/cli/plugin.py §validate

## REQ plugins-yaml-refuses-unknown-keys

IF `plugins.yaml` has a key the system does not read, at any level, THEN the system SHALL refuse the file and name the key.
enforced-by: tests/plugins/test_config.py::test_plugins_yaml_is_refused[unknown-key], tests/plugins/test_config.py::test_plugins_yaml_is_refused[as-by-field-name]
origin: src/kraft/plugins/config.py §PluginsConfig

## REQ a-collection-source-is-explicit

IF a `plugins.yaml` collection has both or neither of `git` and `path`, a `git` that is not a full https, ssh, file or scp-like URL, a `git` with credentials or a leading `-`, a `path` that is not absolute and normal, or a `ref` git would read as an option, THEN the system SHALL refuse the file.
enforced-by: tests/plugins/test_config.py::test_plugins_yaml_is_refused[git-and-path], tests/plugins/test_config.py::test_plugins_yaml_is_refused[neither], tests/plugins/test_config.py::test_plugins_yaml_is_refused[owner-repo], tests/plugins/test_config.py::test_plugins_yaml_is_refused[http], tests/plugins/test_config.py::test_plugins_yaml_is_refused[credentials], tests/plugins/test_config.py::test_plugins_yaml_is_refused[leading-dash], tests/plugins/test_config.py::test_plugins_yaml_is_refused[relative-path], tests/plugins/test_config.py::test_plugins_yaml_is_refused[ref-option]
origin: src/kraft/plugins/config.py §CollectionConfig

## REQ a-collection-auto-update-has-no-exceptions

IF a plugin entry sets `auto_update` while its collection sets `auto_update: true`, THEN the system SHALL refuse `plugins.yaml`.
enforced-by: tests/plugins/test_config.py::test_plugins_yaml_is_refused[opt-out-under-auto-collection]
origin: src/kraft/plugins/config.py §PluginsConfig

## REQ a-directory-collection-never-auto-updates

IF a directory collection sets `auto_update: true`, or a plugin from one sets `auto_update` at all, THEN the system SHALL refuse `plugins.yaml`.
enforced-by: tests/plugins/test_config.py::test_plugins_yaml_is_refused[directory-auto-update], tests/plugins/test_config.py::test_plugins_yaml_is_refused[directory-plugin-auto-update]
origin: src/kraft/plugins/config.py §CollectionConfig

## REQ validate-runs-the-install-checks

WHEN an author validates a local collection or plugin, the system SHALL run the same extraction checks, plugin checks and whole-library lint as an install, SHALL report each problem with its file and key, and SHALL NOT change `plugins.yaml`, `plugins.lock` or the store.
enforced-by: tests/cli/test_plugin.py::test_validate[collection], tests/cli/test_plugin.py::test_validate[single-plugin], tests/cli/test_plugin.py::test_validate[lint-failure], tests/cli/test_plugin.py::test_validate[wrong-kraft-major], tests/cli/test_plugin.py::test_validate[no-home], tests/cli/test_plugin.py::test_validate[refused-content], tests/cli/test_plugin.py::test_validate_writes_nothing_and_prints_json
origin: src/kraft/cli/plugin.py §validate

## REQ plugins-load-only-from-the-lock

The system SHALL load a plugin only when it has both an enabled `plugins.yaml` entry and a lock entry, SHALL load it from the store the lock names, and SHALL NOT fetch a collection when it loads the library.
enforced-by: tests/plugins/test_load.py::test_load_reads_the_lock_not_the_ref, tests/plugins/test_load.py::test_what_loads[listed-not-locked], tests/plugins/test_load.py::test_what_loads[locked-not-listed], tests/plugins/test_load.py::test_what_loads[disabled]
origin: src/kraft/plugins/load.py §installed

## REQ a-broken-plugin-affects-only-itself

IF a plugin fails a load-time check, THEN the system SHALL leave only that plugin out, report it under `invalid_templates`, and load the rest of the library.
enforced-by: tests/plugins/test_load.py::test_a_broken_plugin_library_affects_only_that_plugin, tests/plugins/test_load.py::test_load_time_checks[requires-harness-removed], tests/plugins/test_load.py::test_load_time_checks[repo-id-taken], tests/api/test_deps.py::test_health_names_a_plugin_that_did_not_load[store-deleted]
origin: src/kraft/plugins/load.py §installed

## REQ a-hand-alias-or-ref-change-waits-for-update

WHILE a plugin's `plugins.yaml` alias or its collection's `ref` differs from the lock's, the system SHALL keep loading the plugin under its locked namespace and commit until an update applies the change.
enforced-by: tests/plugins/test_load.py::test_what_loads[alias-change-pending]
origin: src/kraft/plugins/load.py §installed

## REQ a-plugin-whose-digest-does-not-match-is-not-loaded

IF a plugin's extracted files do not match the lock's digest, THEN the system SHALL leave that plugin out and report it under `invalid_templates`.
enforced-by: tests/plugins/test_load.py::test_a_digest_mismatch_leaves_the_plugin_out, tests/api/test_deps.py::test_health_names_a_plugin_that_did_not_load[store-edited]
origin: src/kraft/plugins/load.py §installed

## REQ an-unloaded-plugin-skill-is-never-delegated

IF a skill reference's qualifier is the namespace of a plugin in `plugins.yaml` or `plugins.lock` that did not load, THEN the system SHALL fail validation instead of handing the reference to the agent.
enforced-by: tests/test_skill.py::test_plugin_skill_references[unloaded-plugin-is-an-error], tests/plugins/test_load.py::test_what_loads[disabled]
origin: src/kraft/skill.py §validate

## REQ a-plugin-is-self-contained

IF a plugin's qualified `extends`, `steering`, `profile` or `skill` reference names another installed Kraft plugin's namespace, THEN the system SHALL refuse the plugin.
enforced-by: tests/plugins/test_update.py::test_a_plugin_cannot_carry[reference-to-another-plugin], tests/plugins/test_update.py::test_a_skill_may_not_reach_into_another_kraft_plugin[another-kraft-plugin]
origin: src/kraft/plugins/update.py §check

## REQ a-plugin-cannot-run-commands-or-grant-permissions

IF a plugin declares a `subprocess` task, sets a `policy:` key other than a time cap, `timeout_minutes`, `max_attempts`, `budget_usd`, `token_budget` or `deny_tools` at any scope, or names a harness not in its `requires.harnesses`, THEN the system SHALL refuse the plugin.
enforced-by: tests/plugins/test_update.py::test_a_plugin_cannot_carry[subprocess], tests/plugins/test_update.py::test_a_plugin_cannot_carry[sandbox], tests/plugins/test_update.py::test_a_plugin_cannot_carry[unrestricted-network], tests/plugins/test_update.py::test_a_plugin_cannot_carry[grants], tests/plugins/test_update.py::test_a_plugin_cannot_carry[allowed-tools], tests/plugins/test_update.py::test_a_plugin_cannot_carry[allowed-harnesses], tests/plugins/test_update.py::test_a_plugin_cannot_carry[escalation-harness], tests/plugins/test_update.py::test_a_plugin_cannot_carry[unknown-policy-key], tests/plugins/test_update.py::test_a_plugin_cannot_carry[gate-scope], tests/plugins/test_update.py::test_a_plugin_cannot_carry[judge-task], tests/plugins/test_update.py::test_a_plugin_cannot_carry[unlisted-harness]
origin: src/kraft/plugins/update.py §check

## REQ update-shows-what-will-run-before-applying

WHEN an install or update would change an installed plugin, the system SHALL list, before it writes anything, its changes to gates, merge steps, the order of a chain's nodes, forge targets, task harnesses and models, policy limits, `requires`, prompts, steering, skills, profiles and plugin skill references, the references its namespace captures, and whether it is a downgrade.
enforced-by: tests/plugins/test_review.py::test_review_calls_out[gate-removed], tests/plugins/test_review.py::test_review_calls_out[gate-replaced], tests/plugins/test_review.py::test_review_calls_out[auto-review], tests/plugins/test_review.py::test_review_calls_out[merge-step], tests/plugins/test_review.py::test_review_calls_out[forge-target], tests/plugins/test_review.py::test_review_calls_out[harness], tests/plugins/test_review.py::test_review_calls_out[limit-raised], tests/plugins/test_review.py::test_review_calls_out[requires], tests/plugins/test_review.py::test_review_calls_out[prompt], tests/plugins/test_review.py::test_review_calls_out[steering], tests/plugins/test_review.py::test_review_calls_out[skill], tests/plugins/test_review.py::test_review_calls_out[profile], tests/plugins/test_review.py::test_review_calls_out[plugin-ref], tests/plugins/test_review.py::test_review_calls_out[downgrade], tests/plugins/test_review.py::test_a_reordered_chain_is_reviewed[gate-after-merge], tests/plugins/test_review.py::test_a_reordered_chain_is_reviewed[unguarded-nodes-swapped]
origin: src/kraft/plugins/review.py §review

## REQ the-review-cannot-hide-text

WHEN the system prints a review, it SHALL escape every control, bidi and format character in it.
enforced-by: tests/plugins/test_review.py::test_review_escapes_what_it_prints
origin: src/kraft/plugins/review.py §render
