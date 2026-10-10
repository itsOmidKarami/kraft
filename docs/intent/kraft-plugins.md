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
enforced-by: tests/templates/test_library_plugins.py::test_plugin_layer_resolution[alias-from-outside], tests/templates/test_library_plugins.py::test_plugin_layer_resolution[alias-own-qualified-ref], tests/cli/test_plugin.py::test_install_as_alias
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
enforced-by: tests/plugins/test_manifest.py::test_a_plugin_is_refused[kraft-name], tests/plugins/test_config.py::test_plugins_yaml_is_refused[kraft-alias], tests/plugins/test_update.py::test_a_namespace_is_refused[kraft]
origin: src/kraft/plugins/config.py §PluginsConfig

## REQ plugin-namespaces-are-unique

IF a plugin's namespace equals another installed plugin's namespace or a `repos.yaml` entry's `id`, THEN the system SHALL refuse the install.
enforced-by: tests/plugins/test_config.py::test_plugins_yaml_is_refused[duplicate-namespace], tests/plugins/test_update.py::test_a_namespace_is_refused[taken-by-alias], tests/plugins/test_update.py::test_a_namespace_is_refused[held-by-a-pending-alias], tests/plugins/test_update.py::test_a_namespace_is_refused[taken-by-repo-id], tests/cli/test_plugin.py::test_validate_against_a_kraft_home[namespace-taken]
origin: src/kraft/plugins/config.py §PluginsConfig

## REQ a-plugin-runs-on-one-kraft-major

IF the running Kraft's release is below a plugin's `requires.kraft` version or in a different major, THEN the system SHALL refuse the plugin at validate, install and update, and SHALL leave it out at load.
enforced-by: tests/plugins/test_manifest.py::test_kraft_compatibility[below-minimum], tests/plugins/test_manifest.py::test_kraft_compatibility[next-major], tests/plugins/test_manifest.py::test_kraft_compatibility[earlier-major], tests/plugins/test_manifest.py::test_kraft_compatibility[release-candidate], tests/plugins/test_load.py::test_a_plugin_for_another_major_is_left_out, tests/plugins/test_update.py::test_an_update_for_another_major_keeps_the_lock
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
enforced-by: tests/plugins/test_config.py::test_plugins_yaml_is_refused[git-and-path], tests/plugins/test_config.py::test_plugins_yaml_is_refused[neither], tests/plugins/test_config.py::test_plugins_yaml_is_refused[owner-repo], tests/plugins/test_config.py::test_plugins_yaml_is_refused[http], tests/plugins/test_config.py::test_plugins_yaml_is_refused[credentials], tests/plugins/test_config.py::test_plugins_yaml_is_refused[leading-dash], tests/plugins/test_config.py::test_plugins_yaml_is_refused[relative-path], tests/plugins/test_config.py::test_plugins_yaml_is_refused[ref-option], tests/cli/test_plugin.py::test_collection_add_writes_a_full_source[owner-repo], tests/cli/test_plugin.py::test_collection_add_writes_a_full_source[directory], tests/cli/test_plugin.py::test_collection_add_writes_a_full_source[ref]
origin: src/kraft/plugins/config.py §CollectionConfig

## REQ a-collection-auto-update-has-no-exceptions

IF a plugin entry sets `auto_update` while its collection sets `auto_update: true`, THEN the system SHALL refuse `plugins.yaml`.
enforced-by: tests/plugins/test_config.py::test_plugins_yaml_is_refused[opt-out-under-auto-collection], tests/cli/test_plugin.py::test_auto_update_verbs[plugin-on-under-auto-collection-refused], tests/cli/test_plugin.py::test_auto_update_verbs[collection-on-with-plugin-settings-refused]
origin: src/kraft/plugins/config.py §PluginsConfig

## REQ a-directory-collection-never-auto-updates

IF a directory collection sets `auto_update: true`, or a plugin from one sets `auto_update` at all, THEN the system SHALL refuse `plugins.yaml`.
enforced-by: tests/plugins/test_config.py::test_plugins_yaml_is_refused[directory-auto-update], tests/plugins/test_config.py::test_plugins_yaml_is_refused[directory-plugin-auto-update]
origin: src/kraft/plugins/config.py §CollectionConfig

## REQ validate-runs-the-install-checks

WHEN an author validates a local collection or plugin, the system SHALL run the same extraction checks, plugin checks and whole-library lint as an install, SHALL report each problem with its file and key, and SHALL NOT change `plugins.yaml`, `plugins.lock` or the store.
enforced-by: tests/cli/test_plugin.py::test_validate[collection], tests/cli/test_plugin.py::test_validate[single-plugin], tests/cli/test_plugin.py::test_validate[lint-failure], tests/cli/test_plugin.py::test_validate[wrong-kraft-major], tests/cli/test_plugin.py::test_validate[no-home], tests/cli/test_plugin.py::test_validate[refused-content], tests/cli/test_plugin.py::test_validate_writes_nothing_and_prints_json, tests/cli/test_plugin.py::test_validate_against_a_kraft_home[same-id-is-not-a-clash], tests/cli/test_plugin.py::test_validate_against_a_kraft_home[requires-unmet], tests/cli/test_plugin.py::test_validate_against_a_kraft_home[breaks-a-chain]
origin: src/kraft/cli/plugin.py §validate

## REQ plugins-load-only-from-the-lock

The system SHALL load a plugin only when it has both an enabled `plugins.yaml` entry and a lock entry, SHALL load it from the store the lock names, and SHALL NOT fetch a collection when it loads the library.
enforced-by: tests/plugins/test_load.py::test_load_reads_the_lock_not_the_ref, tests/plugins/test_load.py::test_what_loads[listed-not-locked], tests/plugins/test_load.py::test_what_loads[locked-not-listed], tests/plugins/test_load.py::test_what_loads[disabled]
origin: src/kraft/plugins/load.py §installed

## REQ a-broken-plugin-affects-only-itself

IF a plugin fails a load-time check, THEN the system SHALL leave only that plugin out, report it under `invalid_templates`, and load the rest of the library.
enforced-by: tests/plugins/test_load.py::test_a_broken_plugin_library_affects_only_that_plugin, tests/plugins/test_load.py::test_load_time_checks[requires-harness-removed], tests/plugins/test_load.py::test_load_time_checks[repo-id-taken], tests/api/test_deps.py::test_health_names_a_plugin_that_did_not_load[store-deleted], tests/plugins/test_load.py::test_a_limit_above_a_lowered_maximum_leaves_the_plugin_out
origin: src/kraft/plugins/load.py §installed

## REQ a-hand-alias-or-ref-change-waits-for-update

WHILE a plugin's `plugins.yaml` alias or its collection's `ref` differs from the lock's, the system SHALL keep loading the plugin under its locked namespace and commit until an update applies the change.
enforced-by: tests/plugins/test_load.py::test_what_loads[alias-change-pending], tests/plugins/test_update.py::test_a_ref_change_is_reviewed_whatever_the_version
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
enforced-by: tests/plugins/test_update.py::test_a_plugin_cannot_carry[reference-to-another-plugin], tests/plugins/test_update.py::test_a_skill_may_not_reach_into_another_kraft_plugin[another-kraft-plugin], tests/cli/test_plugin.py::test_validate_against_a_kraft_home[dependency]
origin: src/kraft/plugins/update.py §check

## REQ a-plugin-cannot-run-commands-or-grant-permissions

IF a plugin declares a `subprocess` task, sets a `policy:` key other than a time cap, `timeout_minutes`, `max_attempts`, `budget_usd`, `token_budget` or `deny_tools` at any scope, or names a harness not in its `requires.harnesses`, THEN the system SHALL refuse the plugin.
enforced-by: tests/plugins/test_update.py::test_a_plugin_cannot_carry[subprocess], tests/plugins/test_update.py::test_a_plugin_cannot_carry[sandbox], tests/plugins/test_update.py::test_a_plugin_cannot_carry[unrestricted-network], tests/plugins/test_update.py::test_a_plugin_cannot_carry[grants], tests/plugins/test_update.py::test_a_plugin_cannot_carry[allowed-tools], tests/plugins/test_update.py::test_a_plugin_cannot_carry[allowed-harnesses], tests/plugins/test_update.py::test_a_plugin_cannot_carry[escalation-harness], tests/plugins/test_update.py::test_a_plugin_cannot_carry[unknown-policy-key], tests/plugins/test_update.py::test_a_plugin_cannot_carry[gate-scope], tests/plugins/test_update.py::test_a_plugin_cannot_carry[judge-task], tests/plugins/test_update.py::test_a_plugin_cannot_carry[unlisted-harness]
origin: src/kraft/plugins/update.py §check

## REQ update-shows-what-will-run-before-applying

WHEN an install or update would change an installed plugin, the system SHALL list, before it writes anything, its changes to gates, merge steps, the order of a chain's nodes, forge targets, task harnesses and models, policy limits, `requires`, prompts, steering, skills, profiles and plugin skill references, the references its namespace captures, and whether it is a downgrade.
enforced-by: tests/plugins/test_review.py::test_review_calls_out[gate-removed], tests/plugins/test_review.py::test_review_calls_out[gate-replaced], tests/plugins/test_review.py::test_review_calls_out[auto-review], tests/plugins/test_review.py::test_review_calls_out[merge-step], tests/plugins/test_review.py::test_review_calls_out[forge-target], tests/plugins/test_review.py::test_review_calls_out[target-used-by-a-sibling-chain], tests/plugins/test_review.py::test_review_calls_out[harness], tests/plugins/test_review.py::test_review_calls_out[limit-raised], tests/plugins/test_review.py::test_review_calls_out[requires], tests/plugins/test_review.py::test_review_calls_out[prompt], tests/plugins/test_review.py::test_review_calls_out[steering], tests/plugins/test_review.py::test_review_calls_out[skill], tests/plugins/test_review.py::test_review_calls_out[profile], tests/plugins/test_review.py::test_review_calls_out[plugin-ref], tests/plugins/test_review.py::test_review_calls_out[downgrade], tests/plugins/test_review.py::test_a_reordered_chain_is_reviewed[gate-after-merge], tests/plugins/test_review.py::test_a_reordered_chain_is_reviewed[work-moved-past-a-gate], tests/plugins/test_update.py::test_the_review_covers_the_instances_own_chains[captured-ref], tests/plugins/test_update.py::test_the_review_covers_the_instances_own_chains[local-chain-through-the-plugin]
origin: src/kraft/plugins/review.py §review

## REQ the-review-cannot-hide-text

WHEN the system prints a review, it SHALL escape every control, bidi and format character in it.
enforced-by: tests/plugins/test_review.py::test_review_escapes_what_it_prints
origin: src/kraft/plugins/review.py §render

## REQ a-plugin-limit-above-maxima-is-refused

IF a plugin's policy limit exceeds the instance's `maxima:`, THEN the system SHALL refuse the plugin.
enforced-by: tests/plugins/test_update.py::test_a_limit_above_the_instance_maxima_is_refused
origin: src/kraft/plugins/update.py §_breaks

## REQ a-left-out-plugin-still-updates

WHILE an installed plugin is left out at load, the system SHALL still check it for updates.
enforced-by: tests/plugins/test_update.py::test_a_left_out_plugin_still_updates
origin: src/kraft/plugins/update.py §update

## REQ an-update-waits-for-a-version-change

WHERE a plugin comes from a git collection, the system SHALL treat it as having an update only when its version differs from the version in `plugins.lock`.
enforced-by: tests/plugins/test_update.py::test_an_update_waits_for_a_version_change[same-version-new-commit], tests/plugins/test_update.py::test_an_update_waits_for_a_version_change[version-raised], tests/plugins/test_update.py::test_an_update_waits_for_a_version_change[version-lowered]
origin: src/kraft/plugins/update.py §update

## REQ a-directory-collection-updates-on-every-change

WHERE a plugin comes from a directory collection, the system SHALL treat any change to its digest as an update, whatever its version.
enforced-by: tests/plugins/test_update.py::test_an_update_waits_for_a_version_change[directory-collection-updates-on-change]
origin: src/kraft/plugins/update.py §update

## REQ re-install-takes-the-newest-commit

WHEN an operator re-installs a plugin, the system SHALL resolve its collection's `ref` again even if the version is unchanged, and SHALL review it like any update.
enforced-by: tests/plugins/test_update.py::test_re_install_takes_the_newest_commit, tests/cli/test_plugin.py::test_install_re_install[refused-without-flag], tests/cli/test_plugin.py::test_install_re_install[takes-newest-commit]
origin: src/kraft/plugins/update.py §update

## REQ update-requires-acceptance

IF an install or update is declined, refused, or run with `--check`, THEN the system SHALL NOT change `plugins.yaml`, `plugins.lock` or the store.
enforced-by: tests/plugins/test_update.py::test_a_declined_update_writes_nothing[install], tests/plugins/test_update.py::test_a_declined_update_writes_nothing[update], tests/plugins/test_update.py::test_a_batch_continues_past_a_refused_plugin, tests/cli/test_plugin.py::test_a_refused_install_writes_nothing, tests/cli/test_plugin.py::test_update_check_exit_codes[update-waiting], tests/cli/test_plugin.py::test_no_terminal_without_yes_declines
origin: src/kraft/plugins/update.py §update

## REQ update-refuses-a-change-that-breaks-a-chain

IF an update's candidate plugins would leave a currently resolving chain, chain reference or profile fallback unresolved, THEN the system SHALL refuse that plugin and name each such reference.
enforced-by: tests/plugins/test_update.py::test_an_update_that_breaks_a_reference_is_refused[local-chain], tests/plugins/test_update.py::test_an_update_that_breaks_a_reference_is_refused[repos-default-chain], tests/plugins/test_update.py::test_an_update_that_breaks_a_reference_is_refused[intake-schedule], tests/plugins/test_update.py::test_a_captured_reference_the_plugin_does_not_provide_is_refused, tests/plugins/test_update.py::test_a_broken_profile_pairing_is_refused
origin: src/kraft/plugins/update.py §_breaks

## REQ one-plugin-change-at-a-time

WHILE one process writes plugin state, the system SHALL make a second writer wait, then fail, and SHALL write nothing IF the plugin files changed since the review was built.
enforced-by: tests/plugins/test_update.py::test_a_second_writer_waits_then_fails, tests/plugins/test_update.py::test_a_lock_changed_during_review_writes_nothing
origin: src/kraft/plugins/update.py §write_lock

## REQ update-check-exit-codes

WHEN `update --check` finishes, the system SHALL exit 0 when every plugin is current, 1 when an update is waiting, 2 when an update would be refused, and 3 when it could not check.
enforced-by: tests/cli/test_plugin.py::test_update_check_exit_codes[up-to-date], tests/cli/test_plugin.py::test_update_check_exit_codes[update-waiting], tests/cli/test_plugin.py::test_update_check_exit_codes[refused], tests/cli/test_plugin.py::test_update_check_exit_codes[cannot-check]
origin: src/kraft/cli/plugin.py §_cmd_update

## REQ removing-a-referenced-plugin-is-refused

IF local configuration (`library.yaml`, `chains/`, `repos.yaml`, `intake.yaml`, `policy.yaml` triggers or `harnesses.yaml` fallbacks) references the namespace of a plugin being uninstalled or disabled, THEN the system SHALL refuse and list each reference.
enforced-by: tests/cli/test_plugin.py::test_removing_a_referenced_plugin_is_refused[uninstall], tests/cli/test_plugin.py::test_removing_a_referenced_plugin_is_refused[disable]
origin: src/kraft/cli/plugin.py §_still_referenced

## REQ a-worker-does-not-change-plugins

WHILE `KRAFT_WORK_ITEM_ID` is set, the system SHALL refuse every mutating `kraft admin plugin` verb.
enforced-by: tests/cli/test_plugin.py::test_a_worker_cannot_change_plugins[collection-add], tests/cli/test_plugin.py::test_a_worker_cannot_change_plugins[collection-auto-update], tests/cli/test_plugin.py::test_a_worker_cannot_change_plugins[collection-remove], tests/cli/test_plugin.py::test_a_worker_cannot_change_plugins[install], tests/cli/test_plugin.py::test_a_worker_cannot_change_plugins[update], tests/cli/test_plugin.py::test_a_worker_cannot_change_plugins[auto-update], tests/cli/test_plugin.py::test_a_worker_cannot_change_plugins[enable], tests/cli/test_plugin.py::test_a_worker_cannot_change_plugins[disable], tests/cli/test_plugin.py::test_a_worker_cannot_change_plugins[uninstall]
origin: src/kraft/cli/plugin.py §_not_a_worker

## REQ a-plugin-verb-reloads-only-the-plugins

WHEN a `kraft admin plugin` verb changes what loads, the system SHALL rebuild the running server's library from the new lock and SHALL NOT apply a pending edit of another config file.
enforced-by: tests/api/test_deps.py::test_a_plugin_reload_leaves_the_operators_pending_edits_pending, tests/cli/test_plugin.py::test_install_as_alias
origin: src/kraft/apply.py §reload

## REQ a-plugins-yaml-edit-is-checked

IF a hand edit of `plugins.yaml` would leave a currently resolving chain or reference unresolved, THEN the system SHALL report it on the pending reload with the reason, and SHALL list a changed `plugins.yaml` or `plugins.lock` as a pending reload.
enforced-by: tests/api/test_config_check.py::test_a_plugins_yaml_edit_is_checked[breaks-a-chain], tests/api/test_config_check.py::test_a_plugins_yaml_edit_is_checked[captures-a-reference], tests/api/test_deps.py::test_a_plugin_reload_leaves_the_operators_pending_edits_pending
origin: src/kraft/api/config_check.py §_check_plugins

## REQ an-edit-that-drops-a-plugin-is-reported

IF an edit of `policy.yaml`, `harnesses.yaml` or `repos.yaml` would leave a loaded plugin out, THEN the system SHALL report the plugin and the reason before the edit is applied.
enforced-by: tests/api/test_config_check.py::test_an_edit_that_drops_a_plugin_is_reported[maxima-lowered], tests/api/test_config_check.py::test_an_edit_that_drops_a_plugin_is_reported[required-profile-removed], tests/api/test_config_check.py::test_an_edit_that_drops_a_plugin_is_reported[namespace-becomes-a-repo-id]
origin: src/kraft/api/config_check.py §_check_policy

## REQ a-reload-refuses-a-plugin-file-that-does-not-read

IF `plugins.yaml` or `plugins.lock` does not read, THEN a reload SHALL keep the plugins the running instance has and SHALL leave the file pending with the reason.
enforced-by: tests/api/test_deps.py::test_a_reload_refuses_a_plugin_file_that_does_not_read[plugins.yaml], tests/api/test_deps.py::test_a_reload_refuses_a_plugin_file_that_does_not_read[plugins.lock]
origin: src/kraft/api/deps.py §_reload_templates

## REQ running-item-keeps-its-plugin-versions

WHILE a work item is not terminal, the system SHALL read its plugin skills and plugin agent profiles from the plugin versions its chain was materialized with, and SHALL refuse a skill of a pinned version whose store is gone instead of handing it to the agent.
enforced-by: tests/plugins/test_pins.py::test_a_resolved_chain_pins_the_plugins_it_reads[local-chain], tests/plugins/test_pins.py::test_a_resolved_chain_pins_the_plugins_it_reads[plugin-chain], tests/plugins/test_pins.py::test_a_rebuilt_snapshot_keeps_the_items_pins[retry-override], tests/plugins/test_pins.py::test_a_rebuilt_snapshot_keeps_the_items_pins[chain-revision], tests/plugins/test_pins.py::test_a_launch_reads_the_version_the_item_started_with, tests/plugins/test_pins.py::test_a_pinned_version_that_is_gone_is_refused_not_delegated, tests/plugins/test_pins.py::test_every_launch_is_handed_the_items_pins, tests/plugins/test_pins.py::test_a_revision_resolves_against_pinned_plugins[through-a-pinned-plugin], tests/plugins/test_pins.py::test_a_revision_resolves_against_pinned_plugins[through-a-plugin-not-touched-yet]
origin: src/kraft/adapters/agent.py §resolve_agent_task

## REQ a-missing-store-is-restored-from-its-commit

WHEN a store that the lock or a work item's pin names is missing, the system SHALL restore it from the recorded commit, SHALL load it only if its tree and digest match, and SHALL restore a work item's pinned store before a launch reads it.
enforced-by: tests/plugins/test_load.py::test_a_missing_store_is_restored_from_the_locked_commit[mirror-has-it], tests/plugins/test_load.py::test_a_missing_store_is_restored_from_the_locked_commit[fetched-by-its-id], tests/plugins/test_pins.py::test_a_launch_restores_the_pinned_store_it_needs, tests/api/test_deps.py::test_a_reload_restores_a_store_the_lock_names_and_this_machine_lacks, tests/api/test_deps.py::test_intake_on_a_plugin_whose_store_is_away_says_retry_not_unknown
origin: src/kraft/plugins/load.py §restore

## REQ a-directory-store-is-restored-only-if-unchanged

WHEN a store from a directory collection is missing, the system SHALL restore it from the folder only if the folder matches the locked digest, and SHALL otherwise leave the plugin out until it is updated.
enforced-by: tests/plugins/test_load.py::test_a_directory_store_is_restored_only_if_unchanged[unchanged], tests/plugins/test_load.py::test_a_directory_store_is_restored_only_if_unchanged[changed]
origin: src/kraft/plugins/load.py §restore

## REQ an-unrestorable-pin-stops-the-item

IF a work item's pinned plugin store cannot be restored, THEN the system SHALL stop the launch as a configuration error and name the plugin, its version and why.
enforced-by: tests/plugins/test_pins.py::test_an_unrestorable_pin_stops_the_launch_and_says_why, tests/plugins/test_load.py::test_a_store_that_cannot_be_restored_says_why[commit-not-on-the-remote], tests/plugins/test_load.py::test_a_store_that_cannot_be_restored_says_why[collection-removed]
origin: src/kraft/adapters/agent.py §resolve_agent_task

## REQ gc-keeps-what-an-item-pins

The system SHALL NOT delete a plugin store that the lock, the loaded library or any work item that has not ended names, and SHALL delete no store while the lock cannot be read.
enforced-by: tests/api/test_deps.py::test_gc_keeps_what_the_lock_the_library_and_unfinished_items_read[pinned-by-paused-item], tests/api/test_deps.py::test_gc_keeps_what_the_lock_the_library_and_unfinished_items_read[pinned-by-stopped-item], tests/api/test_deps.py::test_gc_keeps_what_the_lock_the_library_and_unfinished_items_read[pinned-by-ended-item], tests/api/test_deps.py::test_gc_keeps_what_the_lock_the_library_and_unfinished_items_read[pinned-by-nothing], tests/api/test_deps.py::test_gc_runs_after_a_reload_and_never_while_the_lock_does_not_read
origin: src/kraft/api/deps.py §collect_plugin_stores

## REQ auto-update-is-opt-in

WHERE a plugin's auto-update is on, through its collection's `auto_update: true` or its own, the system SHALL update that plugin in the background after the server starts and once a day while it runs, and SHALL NOT fetch a collection none of whose installed plugins auto-update, except to restore a missing store.
enforced-by: tests/plugins/test_auto_update.py::test_who_auto_updates[plugin-on], tests/plugins/test_auto_update.py::test_who_auto_updates[collection-on], tests/plugins/test_auto_update.py::test_who_auto_updates[neither-is-not-fetched], tests/api/test_deps.py::test_the_server_takes_an_auto_update_and_loads_it, tests/api/test_deps.py::test_a_running_server_checks_for_auto_updates_again
origin: src/kraft/plugins/update.py §auto_update

## REQ auto-update-holds-what-widens-a-run

IF an auto-update is a downgrade, a move onto a pre-release, or has a review call-out about what a run can reach, THEN the system SHALL hold it for a person and keep the locked version.
enforced-by: tests/plugins/test_auto_update.py::test_auto_update_holds[gate-removed], tests/plugins/test_auto_update.py::test_auto_update_holds[limit-raised], tests/plugins/test_auto_update.py::test_auto_update_holds[requires], tests/plugins/test_auto_update.py::test_auto_update_holds[downgrade], tests/plugins/test_auto_update.py::test_auto_update_holds[pre-release], tests/plugins/test_auto_update.py::test_a_newer_pre_release_follows_a_pre_release
origin: src/kraft/plugins/update.py §may_apply_unattended

## REQ auto-update-never-installs-or-re-installs

The system SHALL NOT auto-update a plugin that has no lock entry, to a newer commit at an unchanged version, or to a changed alias, ref or collection URL.
enforced-by: tests/plugins/test_auto_update.py::test_auto_update_never[installs], tests/plugins/test_auto_update.py::test_auto_update_never[re-installs], tests/plugins/test_auto_update.py::test_auto_update_never[applies-a-ref-change], tests/plugins/test_auto_update.py::test_auto_update_never[follows-a-new-url]
origin: src/kraft/plugins/update.py §auto_update

## REQ failed-auto-update-keeps-the-locked-version

IF an auto-update fails or is held, THEN the system SHALL keep serving the locked version and SHALL report the outcome in `/health` and `kraft admin doctor` without marking health degraded, until a person applies the update or uninstalls the plugin.
enforced-by: tests/plugins/test_auto_update.py::test_a_failed_auto_update_keeps_the_lock_and_records_why, tests/api/test_deps.py::test_a_held_or_failed_auto_update_is_reported_and_not_degraded, tests/test_doctor_plugins.py::test_the_plugin_updates_row_warns_and_never_fails[held], tests/test_doctor_plugins.py::test_the_plugin_updates_row_warns_and_never_fails[failed-auth], tests/plugins/test_auto_update.py::test_a_person_taking_a_held_update_clears_its_warning, tests/cli/test_plugin.py::test_uninstall_forgets_the_plugins_last_auto_update
origin: src/kraft/plugins/update.py §auto_update

## REQ a-major-update-names-the-plugins-it-drops

WHEN `kraft admin update` would install a release whose major leaves a loaded plugin's `requires.kraft` unmet, the system SHALL list those plugins and ask before installing.
enforced-by: tests/cli/test_admin_update.py::test_a_major_update_lists_the_plugins_it_would_drop[next-major-asks], tests/cli/test_admin_update.py::test_a_major_update_lists_the_plugins_it_would_drop[next-major-accepted], tests/cli/test_admin_update.py::test_a_major_update_lists_the_plugins_it_would_drop[same-major-says-nothing]
origin: src/kraft/cli/admin.py §_confirm_dropped_plugins

## REQ plugin-content-is-read-only

IF a write would save a plugin's component or chain, THEN the system SHALL answer 409 and name the plugin.
enforced-by: tests/api/test_plugin_content.py::test_saving_a_plugin_entry_answers_409[put-chain], tests/api/test_plugin_content.py::test_saving_a_plugin_entry_answers_409[chains-draft], tests/api/test_plugin_content.py::test_saving_a_plugin_entry_answers_409[library-op]
origin: src/kraft/templates/catalogue.py §read_only_message

## REQ a-plugin-copy-resolves-locally

WHEN a plugin component or chain is copied into a local draft, the system SHALL carry its plugin-internal references qualified, so the copy resolves.
enforced-by: tests/api/test_plugin_content.py::test_copy_from_a_plugin[chain], tests/api/test_plugin_content.py::test_copy_from_a_plugin[component]
origin: src/kraft/drafts/ops.py §copy_component
