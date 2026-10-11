# Intent: Kraft add-ons and collections

Teams share chains, library components, skills and agent profiles as Kraft
add-ons listed in a collection. An instance pins each installed add-on to a
commit, changes it only through a reviewed update, and never lets an add-on
change what a run may do. `enforced-by:` names the test that pins each
requirement.

## REQ addon-content-is-addressed-by-namespace

The system SHALL address every component, chain, skill and agent profile a Kraft add-on provides, from outside that add-on, as `<namespace>:<name>`, where the namespace is the add-on's name or its install alias.
enforced-by: tests/templates/test_library_addons.py::test_addon_layer_resolution[local-extends-addon], tests/test_skill.py::test_addon_skill_references[loaded-addon-is-read], tests/templates/test_environment.py::test_addon_profiles[qualified]
origin: src/kraft/templates/library.py §TemplateLibrary.from_yaml_dir

## REQ an-alias-becomes-the-namespace

WHERE an add-on is installed under an alias, the system SHALL address its components, chains, skills and agent profiles by the alias, including the references the add-on makes to its own name.
enforced-by: tests/templates/test_library_addons.py::test_addon_layer_resolution[alias-from-outside], tests/templates/test_library_addons.py::test_addon_layer_resolution[alias-own-qualified-ref], tests/cli/test_addon.py::test_install_as_alias
origin: src/kraft/addons/load.py §qualify

## REQ a-bare-local-name-is-local

WHEN local configuration references a component, chain, steering profile or agent profile by a bare name, the system SHALL NOT resolve it to an add-on's declaration.
enforced-by: tests/templates/test_library_addons.py::test_addon_layer_resolution[bare-local-is-not-an-addon-component]
origin: src/kraft/templates/library.py §TemplateLibrary

## REQ a-local-declaration-is-bare

IF a local chain id, `library.yaml` key or `harnesses.yaml` profile id contains `:`, THEN the system SHALL refuse it.
enforced-by: tests/templates/test_library_addons.py::test_a_local_declaration_with_a_colon_is_refused[chain-id], tests/templates/test_library_addons.py::test_a_local_declaration_with_a_colon_is_refused[library-key], tests/templates/test_library_addons.py::test_a_local_declaration_with_a_colon_is_refused[profile-id]
origin: src/kraft/templates/library.py §_add_chain

## REQ addon-bare-names-resolve-within-the-addon

WHEN an add-on's component references a bare name in `extends`, `steering` or `skill`, the system SHALL resolve it to that add-on's own declaration.
enforced-by: tests/templates/test_library_addons.py::test_addon_layer_resolution[addon-bare-extends], tests/templates/test_library_addons.py::test_addon_layer_resolution[addon-bare-steering], tests/templates/test_library_addons.py::test_addon_layer_resolution[addon-bare-skill]
origin: src/kraft/addons/load.py §qualify

## REQ addon-harness-and-bare-profile-are-the-instances

WHEN an add-on's task names a harness or a bare agent profile, the system SHALL resolve it against the instance's `harnesses.yaml`.
enforced-by: tests/templates/test_library_addons.py::test_addon_layer_resolution[addon-bare-profile-is-instance]
origin: src/kraft/addons/load.py §qualify

## REQ addon-cannot-define-harness-profiles

IF an add-on's profiles file contains a `harnesses` section, THEN the system SHALL refuse the add-on.
enforced-by: tests/addons/test_manifest.py::test_an_addon_is_refused[harnesses-section]
origin: src/kraft/addons/manifest.py §check_addon

## REQ extraction-takes-only-regular-layout-files

WHEN the system extracts an add-on, it SHALL extract only the fixed layout's files, and SHALL refuse the add-on IF one of them is a symlink, a submodule, an LFS pointer, larger than its limit, or carries a control, bidi or format character.
enforced-by: tests/addons/test_fetch.py::test_extraction_refuses[symlink], tests/addons/test_fetch.py::test_extraction_refuses[submodule], tests/addons/test_fetch.py::test_extraction_refuses[lfs-pointer], tests/addons/test_fetch.py::test_extraction_refuses[oversized-yaml], tests/addons/test_fetch.py::test_extraction_refuses[oversized-addon], tests/addons/test_fetch.py::test_extraction_refuses[too-many-files], tests/addons/test_fetch.py::test_extraction_refuses[format-character], tests/addons/test_fetch.py::test_only_the_layout_is_extracted
origin: src/kraft/addons/fetch.py §extract_git

## REQ fetch-runs-no-repository-code

WHEN the system fetches a collection, it SHALL NOT check out a working tree or run a hook.
enforced-by: tests/addons/test_fetch.py::test_fetch_never_checks_out_or_runs_hooks
origin: src/kraft/addons/fetch.py §fetch

## REQ namespace-kraft-is-reserved

IF an add-on is named `kraft` or installed under the alias `kraft`, THEN the system SHALL refuse it.
enforced-by: tests/addons/test_manifest.py::test_an_addon_is_refused[kraft-name], tests/addons/test_config.py::test_addons_yaml_is_refused[kraft-alias], tests/addons/test_update.py::test_a_namespace_is_refused[kraft]
origin: src/kraft/addons/config.py §AddonsConfig

## REQ addon-namespaces-are-unique

IF an add-on's namespace equals another installed add-on's namespace or a `repos.yaml` entry's `id`, THEN the system SHALL refuse the install.
enforced-by: tests/addons/test_config.py::test_addons_yaml_is_refused[duplicate-namespace], tests/addons/test_update.py::test_a_namespace_is_refused[taken-by-alias], tests/addons/test_update.py::test_a_namespace_is_refused[held-by-a-pending-alias], tests/addons/test_update.py::test_a_namespace_is_refused[taken-by-repo-id], tests/cli/test_addon.py::test_validate_against_a_kraft_home[namespace-taken]
origin: src/kraft/addons/config.py §AddonsConfig

## REQ an-addon-runs-on-one-kraft-major

IF the running Kraft's release is below an add-on's `requires.kraft` version or in a different major, THEN the system SHALL refuse the add-on at validate, install and update, and SHALL leave it out at load.
enforced-by: tests/addons/test_manifest.py::test_kraft_compatibility[below-minimum], tests/addons/test_manifest.py::test_kraft_compatibility[next-major], tests/addons/test_manifest.py::test_kraft_compatibility[earlier-major], tests/addons/test_manifest.py::test_kraft_compatibility[release-candidate], tests/addons/test_load.py::test_an_addon_for_another_major_is_left_out, tests/addons/test_update.py::test_an_update_for_another_major_keeps_the_lock
origin: src/kraft/addons/manifest.py §kraft_compatible

## REQ unknown-manifest-keys-are-ignored

The system SHALL ignore a top-level `collection.json` or `addon.json` key it does not read, and SHALL refuse an unknown key inside `requires`.
enforced-by: tests/addons/test_manifest.py::test_unknown_keys_are_ignored[collection], tests/addons/test_manifest.py::test_unknown_keys_are_ignored[addon], tests/addons/test_manifest.py::test_unknown_keys_are_ignored[claude-code-keys], tests/addons/test_manifest.py::test_an_addon_is_refused[unknown-requires-key]
origin: src/kraft/addons/manifest.py §AddonManifest

## REQ validate-warns-about-unknown-keys

WHEN `validate` reads a manifest key Kraft does not read, the system SHALL list it as a warning.
enforced-by: tests/cli/test_addon.py::test_validate[unknown-key-warning]
origin: src/kraft/cli/addon.py §validate

## REQ addons-yaml-refuses-unknown-keys

IF `addons.yaml` has a key the system does not read, at any level, THEN the system SHALL refuse the file and name the key.
enforced-by: tests/addons/test_config.py::test_addons_yaml_is_refused[unknown-key], tests/addons/test_config.py::test_addons_yaml_is_refused[as-by-field-name]
origin: src/kraft/addons/config.py §AddonsConfig

## REQ a-collection-source-is-explicit

IF an `addons.yaml` collection has both or neither of `git` and `path`, a `git` that is not a full https, ssh, file or scp-like URL, a `git` with credentials or a leading `-`, a `path` that is not absolute and normal, or a `ref` git would read as an option, THEN the system SHALL refuse the file.
enforced-by: tests/addons/test_config.py::test_addons_yaml_is_refused[git-and-path], tests/addons/test_config.py::test_addons_yaml_is_refused[neither], tests/addons/test_config.py::test_addons_yaml_is_refused[owner-repo], tests/addons/test_config.py::test_addons_yaml_is_refused[http], tests/addons/test_config.py::test_addons_yaml_is_refused[credentials], tests/addons/test_config.py::test_addons_yaml_is_refused[leading-dash], tests/addons/test_config.py::test_addons_yaml_is_refused[relative-path], tests/addons/test_config.py::test_addons_yaml_is_refused[ref-option], tests/cli/test_addon.py::test_collection_add_writes_a_full_source[owner-repo], tests/cli/test_addon.py::test_collection_add_writes_a_full_source[directory], tests/cli/test_addon.py::test_collection_add_writes_a_full_source[ref]
origin: src/kraft/addons/config.py §CollectionConfig

## REQ a-collection-auto-update-has-no-exceptions

IF an add-on entry sets `auto_update` while its collection sets `auto_update: true`, THEN the system SHALL refuse `addons.yaml`.
enforced-by: tests/addons/test_config.py::test_addons_yaml_is_refused[opt-out-under-auto-collection], tests/cli/test_addon.py::test_auto_update_verbs[addon-on-under-auto-collection-refused], tests/cli/test_addon.py::test_auto_update_verbs[collection-on-with-addon-settings-refused]
origin: src/kraft/addons/config.py §AddonsConfig

## REQ a-directory-collection-never-auto-updates

IF a directory collection sets `auto_update: true`, or an add-on from one sets `auto_update` at all, THEN the system SHALL refuse `addons.yaml`.
enforced-by: tests/addons/test_config.py::test_addons_yaml_is_refused[directory-auto-update], tests/addons/test_config.py::test_addons_yaml_is_refused[directory-addon-auto-update]
origin: src/kraft/addons/config.py §CollectionConfig

## REQ validate-runs-the-install-checks

WHEN an author validates a local collection or add-on, the system SHALL run the same extraction checks, add-on checks and whole-library lint as an install, SHALL report each problem with its file and key, and SHALL NOT change `addons.yaml`, `addons.lock` or the store.
enforced-by: tests/cli/test_addon.py::test_validate[collection], tests/cli/test_addon.py::test_validate[single-addon], tests/cli/test_addon.py::test_validate[lint-failure], tests/cli/test_addon.py::test_validate[wrong-kraft-major], tests/cli/test_addon.py::test_validate[no-home], tests/cli/test_addon.py::test_validate[refused-content], tests/cli/test_addon.py::test_validate_writes_nothing_and_prints_json, tests/cli/test_addon.py::test_validate_against_a_kraft_home[same-id-is-not-a-clash], tests/cli/test_addon.py::test_validate_against_a_kraft_home[requires-unmet], tests/cli/test_addon.py::test_validate_against_a_kraft_home[breaks-a-chain]
origin: src/kraft/cli/addon.py §validate

## REQ addons-load-only-from-the-lock

The system SHALL load an add-on only when it has both an enabled `addons.yaml` entry and a lock entry, SHALL load it from the store the lock names, and SHALL NOT fetch a collection when it loads the library.
enforced-by: tests/addons/test_load.py::test_load_reads_the_lock_not_the_ref, tests/addons/test_load.py::test_what_loads[listed-not-locked], tests/addons/test_load.py::test_what_loads[locked-not-listed], tests/addons/test_load.py::test_what_loads[disabled]
origin: src/kraft/addons/load.py §installed

## REQ a-broken-addon-affects-only-itself

IF an add-on fails a load-time check, THEN the system SHALL leave only that add-on out, report it under `invalid_templates`, and load the rest of the library.
enforced-by: tests/addons/test_load.py::test_a_broken_addon_library_affects_only_that_addon, tests/addons/test_load.py::test_load_time_checks[requires-harness-removed], tests/addons/test_load.py::test_load_time_checks[repo-id-taken], tests/api/test_deps.py::test_health_names_an_addon_that_did_not_load[store-deleted], tests/addons/test_load.py::test_a_limit_above_a_lowered_maximum_leaves_the_addon_out
origin: src/kraft/addons/load.py §installed

## REQ a-hand-alias-or-ref-change-waits-for-update

WHILE an add-on's `addons.yaml` alias or its collection's `ref` differs from the lock's, the system SHALL keep loading the add-on under its locked namespace and commit until an update applies the change.
enforced-by: tests/addons/test_load.py::test_what_loads[alias-change-pending], tests/addons/test_update.py::test_a_ref_change_is_reviewed_whatever_the_version
origin: src/kraft/addons/load.py §installed

## REQ an-addon-whose-digest-does-not-match-is-not-loaded

IF an add-on's extracted files do not match the lock's digest, THEN the system SHALL leave that add-on out and report it under `invalid_templates`.
enforced-by: tests/addons/test_load.py::test_a_digest_mismatch_leaves_the_addon_out, tests/api/test_deps.py::test_health_names_an_addon_that_did_not_load[store-edited]
origin: src/kraft/addons/load.py §installed

## REQ an-unloaded-addon-skill-is-never-delegated

IF a skill reference's qualifier is the namespace of an add-on in `addons.yaml` or `addons.lock` that did not load, THEN the system SHALL fail validation instead of handing the reference to the agent.
enforced-by: tests/test_skill.py::test_addon_skill_references[unloaded-addon-is-an-error], tests/addons/test_load.py::test_what_loads[disabled]
origin: src/kraft/skill.py §validate

## REQ an-addon-is-self-contained

IF an add-on's qualified `extends`, `steering`, `profile` or `skill` reference names another installed Kraft add-on's namespace, THEN the system SHALL refuse the add-on.
enforced-by: tests/addons/test_update.py::test_an_addon_cannot_carry[reference-to-another-addon], tests/addons/test_update.py::test_a_skill_may_not_reach_into_another_kraft_addon[another-kraft-addon], tests/cli/test_addon.py::test_validate_against_a_kraft_home[dependency]
origin: src/kraft/addons/update.py §check

## REQ an-addon-cannot-run-commands-or-grant-permissions

IF an add-on declares a `subprocess` task, sets a `policy:` key other than a time cap, `timeout_minutes`, `max_attempts`, `budget_usd`, `token_budget` or `deny_tools` at any scope, or names a harness not in its `requires.harnesses`, THEN the system SHALL refuse the add-on.
enforced-by: tests/addons/test_update.py::test_an_addon_cannot_carry[subprocess], tests/addons/test_update.py::test_an_addon_cannot_carry[sandbox], tests/addons/test_update.py::test_an_addon_cannot_carry[unrestricted-network], tests/addons/test_update.py::test_an_addon_cannot_carry[grants], tests/addons/test_update.py::test_an_addon_cannot_carry[allowed-tools], tests/addons/test_update.py::test_an_addon_cannot_carry[allowed-harnesses], tests/addons/test_update.py::test_an_addon_cannot_carry[escalation-harness], tests/addons/test_update.py::test_an_addon_cannot_carry[unknown-policy-key], tests/addons/test_update.py::test_an_addon_cannot_carry[gate-scope], tests/addons/test_update.py::test_an_addon_cannot_carry[judge-task], tests/addons/test_update.py::test_an_addon_cannot_carry[unlisted-harness]
origin: src/kraft/addons/update.py §check

## REQ update-shows-what-will-run-before-applying

WHEN an install or update would change an installed add-on, the system SHALL list, before it writes anything, its changes to gates, merge steps, the order of a chain's nodes, forge targets, task harnesses and models, policy limits, `requires`, prompts, steering, skills, profiles and add-on skill references, the references its namespace captures, and whether it is a downgrade.
enforced-by: tests/addons/test_review.py::test_review_calls_out[gate-removed], tests/addons/test_review.py::test_review_calls_out[gate-replaced], tests/addons/test_review.py::test_review_calls_out[auto-review], tests/addons/test_review.py::test_review_calls_out[merge-step], tests/addons/test_review.py::test_review_calls_out[forge-target], tests/addons/test_review.py::test_review_calls_out[target-used-by-a-sibling-chain], tests/addons/test_review.py::test_review_calls_out[harness], tests/addons/test_review.py::test_review_calls_out[limit-raised], tests/addons/test_review.py::test_review_calls_out[requires], tests/addons/test_review.py::test_review_calls_out[prompt], tests/addons/test_review.py::test_review_calls_out[steering], tests/addons/test_review.py::test_review_calls_out[skill], tests/addons/test_review.py::test_review_calls_out[profile], tests/addons/test_review.py::test_review_calls_out[plugin-ref], tests/addons/test_review.py::test_review_calls_out[downgrade], tests/addons/test_review.py::test_a_reordered_chain_is_reviewed[gate-after-merge], tests/addons/test_review.py::test_a_reordered_chain_is_reviewed[work-moved-past-a-gate], tests/addons/test_update.py::test_the_review_covers_the_instances_own_chains[captured-ref], tests/addons/test_update.py::test_the_review_covers_the_instances_own_chains[local-chain-through-the-addon]
origin: src/kraft/addons/review.py §review

## REQ the-review-cannot-hide-text

WHEN the system prints a review, it SHALL escape every control, bidi and format character in it.
enforced-by: tests/addons/test_review.py::test_review_escapes_what_it_prints
origin: src/kraft/addons/review.py §render

## REQ an-addon-limit-above-maxima-is-refused

IF an add-on's policy limit exceeds the instance's `maxima:`, THEN the system SHALL refuse the add-on.
enforced-by: tests/addons/test_update.py::test_a_limit_above_the_instance_maxima_is_refused
origin: src/kraft/addons/update.py §breaks

## REQ a-left-out-addon-still-updates

WHILE an installed add-on is left out at load, the system SHALL still check it for updates.
enforced-by: tests/addons/test_update.py::test_a_left_out_addon_still_updates
origin: src/kraft/addons/update.py §update

## REQ an-update-waits-for-a-version-change

WHERE an add-on comes from a git collection, the system SHALL treat it as having an update only when its version differs from the version in `addons.lock`.
enforced-by: tests/addons/test_update.py::test_an_update_waits_for_a_version_change[same-version-new-commit], tests/addons/test_update.py::test_an_update_waits_for_a_version_change[version-raised], tests/addons/test_update.py::test_an_update_waits_for_a_version_change[version-lowered]
origin: src/kraft/addons/update.py §update

## REQ a-directory-collection-updates-on-every-change

WHERE an add-on comes from a directory collection, the system SHALL treat any change to its digest as an update, whatever its version.
enforced-by: tests/addons/test_update.py::test_an_update_waits_for_a_version_change[directory-collection-updates-on-change]
origin: src/kraft/addons/update.py §update

## REQ re-install-takes-the-newest-commit

WHEN an operator re-installs an add-on, the system SHALL resolve its collection's `ref` again even if the version is unchanged, and SHALL review it like any update.
enforced-by: tests/addons/test_update.py::test_re_install_takes_the_newest_commit, tests/cli/test_addon.py::test_install_re_install[refused-without-flag], tests/cli/test_addon.py::test_install_re_install[takes-newest-commit]
origin: src/kraft/addons/update.py §update

## REQ update-requires-acceptance

IF an install or update is declined, refused, or run with `--check`, THEN the system SHALL NOT change `addons.yaml`, `addons.lock` or the store.
enforced-by: tests/addons/test_update.py::test_a_declined_update_writes_nothing[install], tests/addons/test_update.py::test_a_declined_update_writes_nothing[update], tests/addons/test_update.py::test_a_batch_continues_past_a_refused_addon, tests/cli/test_addon.py::test_a_refused_install_writes_nothing, tests/cli/test_addon.py::test_update_check_exit_codes[update-waiting], tests/cli/test_addon.py::test_no_terminal_without_yes_declines
origin: src/kraft/addons/update.py §update

## REQ update-refuses-a-change-that-breaks-a-chain

IF an update's candidate add-ons would leave a currently resolving chain, chain reference or profile fallback unresolved, THEN the system SHALL refuse that add-on and name each such reference.
enforced-by: tests/addons/test_update.py::test_an_update_that_breaks_a_reference_is_refused[local-chain], tests/addons/test_update.py::test_an_update_that_breaks_a_reference_is_refused[repos-default-chain], tests/addons/test_update.py::test_an_update_that_breaks_a_reference_is_refused[intake-schedule], tests/addons/test_update.py::test_a_captured_reference_the_addon_does_not_provide_is_refused, tests/addons/test_update.py::test_a_broken_profile_pairing_is_refused
origin: src/kraft/addons/update.py §breaks

## REQ one-addon-change-at-a-time

WHILE one process writes add-on state, the system SHALL make a second writer wait, then fail, and SHALL write nothing IF the add-on files changed since the review was built.
enforced-by: tests/addons/test_update.py::test_a_second_writer_waits_then_fails, tests/addons/test_update.py::test_a_lock_changed_during_review_writes_nothing
origin: src/kraft/addons/update.py §write_lock

## REQ update-check-exit-codes

WHEN `update --check` finishes, the system SHALL exit 0 when every add-on is current, 1 when an update is waiting, 2 when an update would be refused, and 3 when it could not check.
enforced-by: tests/cli/test_addon.py::test_update_check_exit_codes[up-to-date], tests/cli/test_addon.py::test_update_check_exit_codes[update-waiting], tests/cli/test_addon.py::test_update_check_exit_codes[refused], tests/cli/test_addon.py::test_update_check_exit_codes[cannot-check]
origin: src/kraft/cli/addon.py §_cmd_update

## REQ removing-a-referenced-addon-is-refused

IF local configuration (`library.yaml`, `chains/`, `repos.yaml`, `intake.yaml`, `policy.yaml` triggers or `harnesses.yaml` fallbacks) references the namespace of an add-on being uninstalled or disabled, THEN the system SHALL refuse and list each reference.
enforced-by: tests/cli/test_addon.py::test_removing_a_referenced_addon_is_refused[uninstall], tests/cli/test_addon.py::test_removing_a_referenced_addon_is_refused[disable]
origin: src/kraft/cli/addon.py §_still_referenced

## REQ a-worker-does-not-change-addons

WHILE `KRAFT_WORK_ITEM_ID` is set, the system SHALL refuse every mutating `kraft admin addon` verb.
enforced-by: tests/cli/test_addon.py::test_a_worker_cannot_change_addons[collection-add], tests/cli/test_addon.py::test_a_worker_cannot_change_addons[collection-auto-update], tests/cli/test_addon.py::test_a_worker_cannot_change_addons[collection-remove], tests/cli/test_addon.py::test_a_worker_cannot_change_addons[install], tests/cli/test_addon.py::test_a_worker_cannot_change_addons[update], tests/cli/test_addon.py::test_a_worker_cannot_change_addons[auto-update], tests/cli/test_addon.py::test_a_worker_cannot_change_addons[enable], tests/cli/test_addon.py::test_a_worker_cannot_change_addons[disable], tests/cli/test_addon.py::test_a_worker_cannot_change_addons[uninstall]
origin: src/kraft/cli/addon.py §_not_a_worker

## REQ an-addon-verb-reloads-only-the-addons

WHEN a `kraft admin addon` verb changes what loads, the system SHALL rebuild the running server's library from the new lock and SHALL NOT apply a pending edit of another config file.
enforced-by: tests/api/test_deps.py::test_an_addon_reload_leaves_the_operators_pending_edits_pending, tests/cli/test_addon.py::test_install_as_alias
origin: src/kraft/apply.py §reload

## REQ an-addons-yaml-edit-is-checked

IF a hand edit of `addons.yaml` would leave a currently resolving chain or reference unresolved, THEN the system SHALL report it on the pending reload with the reason, and SHALL list a changed `addons.yaml` or `addons.lock` as a pending reload.
enforced-by: tests/api/test_config_check.py::test_an_addons_yaml_edit_is_checked[breaks-a-chain], tests/api/test_config_check.py::test_an_addons_yaml_edit_is_checked[captures-a-reference], tests/api/test_deps.py::test_an_addon_reload_leaves_the_operators_pending_edits_pending
origin: src/kraft/api/config_check.py §_check_addons

## REQ an-edit-that-drops-an-addon-is-reported

IF an edit of `policy.yaml`, `harnesses.yaml` or `repos.yaml` would leave a loaded add-on out, THEN the system SHALL report the add-on and the reason before the edit is applied.
enforced-by: tests/api/test_config_check.py::test_an_edit_that_drops_an_addon_is_reported[maxima-lowered], tests/api/test_config_check.py::test_an_edit_that_drops_an_addon_is_reported[required-profile-removed], tests/api/test_config_check.py::test_an_edit_that_drops_an_addon_is_reported[namespace-becomes-a-repo-id]
origin: src/kraft/api/config_check.py §_check_policy

## REQ a-reload-refuses-an-addon-file-that-does-not-read

IF `addons.yaml` or `addons.lock` does not read, THEN a reload SHALL keep the add-ons the running instance has and SHALL leave the file pending with the reason.
enforced-by: tests/api/test_deps.py::test_a_reload_refuses_an_addon_file_that_does_not_read[addons.yaml], tests/api/test_deps.py::test_a_reload_refuses_an_addon_file_that_does_not_read[addons.lock]
origin: src/kraft/api/deps.py §_reload_templates

## REQ running-item-keeps-its-addon-versions

WHILE a work item is not terminal, the system SHALL read its add-on skills and add-on agent profiles from the add-on versions its chain was materialized with, and SHALL refuse a skill of a pinned version whose store is gone instead of handing it to the agent.
enforced-by: tests/addons/test_pins.py::test_a_resolved_chain_pins_the_addons_it_reads[local-chain], tests/addons/test_pins.py::test_a_resolved_chain_pins_the_addons_it_reads[addon-chain], tests/addons/test_pins.py::test_a_rebuilt_snapshot_keeps_the_items_pins[retry-override], tests/addons/test_pins.py::test_a_rebuilt_snapshot_keeps_the_items_pins[chain-revision], tests/addons/test_pins.py::test_a_launch_reads_the_version_the_item_started_with, tests/addons/test_pins.py::test_a_pinned_version_that_is_gone_is_refused_not_delegated, tests/addons/test_pins.py::test_every_launch_is_handed_the_items_pins, tests/addons/test_pins.py::test_a_revision_resolves_against_pinned_addons[through-a-pinned-addon], tests/addons/test_pins.py::test_a_revision_resolves_against_pinned_addons[through-an-addon-not-touched-yet], tests/addons/test_pins.py::test_a_chain_through_a_repo_library_pins_its_commit_and_is_restored
origin: src/kraft/adapters/agent.py §resolve_agent_task

## REQ a-missing-store-is-restored-from-its-commit

WHEN a store that the lock or a work item's pin names is missing, the system SHALL restore it from the recorded commit, SHALL load it only if its tree and digest match, and SHALL restore a work item's pinned store before a launch reads it.
enforced-by: tests/addons/test_load.py::test_a_missing_store_is_restored_from_the_locked_commit[mirror-has-it], tests/addons/test_load.py::test_a_missing_store_is_restored_from_the_locked_commit[fetched-by-its-id], tests/addons/test_pins.py::test_a_launch_restores_the_pinned_store_it_needs, tests/api/test_deps.py::test_a_reload_restores_a_store_the_lock_names_and_this_machine_lacks, tests/api/test_deps.py::test_intake_on_an_addon_whose_store_is_away_says_retry_not_unknown, tests/addons/test_load.py::test_a_missing_repo_library_store_is_restored_from_the_checkout, tests/addons/test_pins.py::test_a_chain_through_a_repo_library_pins_its_commit_and_is_restored
origin: src/kraft/addons/load.py §restore

## REQ a-directory-store-is-restored-only-if-unchanged

WHEN a store from a directory collection is missing, the system SHALL restore it from the folder only if the folder matches the locked digest, and SHALL otherwise leave the add-on out until it is updated.
enforced-by: tests/addons/test_load.py::test_a_directory_store_is_restored_only_if_unchanged[unchanged], tests/addons/test_load.py::test_a_directory_store_is_restored_only_if_unchanged[changed]
origin: src/kraft/addons/load.py §restore

## REQ an-unrestorable-pin-stops-the-item

IF a work item's pinned add-on store cannot be restored, THEN the system SHALL stop the launch as a configuration error and name the add-on, its version and why.
enforced-by: tests/addons/test_pins.py::test_an_unrestorable_pin_stops_the_launch_and_says_why, tests/addons/test_load.py::test_a_store_that_cannot_be_restored_says_why[commit-not-on-the-remote], tests/addons/test_load.py::test_a_store_that_cannot_be_restored_says_why[collection-removed]
origin: src/kraft/adapters/agent.py §resolve_agent_task

## REQ gc-keeps-what-an-item-pins

The system SHALL NOT delete an add-on store that the lock, the loaded library or any work item that has not ended names, and SHALL delete no store while the lock cannot be read.
enforced-by: tests/api/test_deps.py::test_gc_keeps_what_the_lock_the_library_and_unfinished_items_read[pinned-by-paused-item], tests/api/test_deps.py::test_gc_keeps_what_the_lock_the_library_and_unfinished_items_read[pinned-by-stopped-item], tests/api/test_deps.py::test_gc_keeps_what_the_lock_the_library_and_unfinished_items_read[pinned-by-ended-item], tests/api/test_deps.py::test_gc_keeps_what_the_lock_the_library_and_unfinished_items_read[pinned-by-nothing], tests/api/test_deps.py::test_gc_runs_after_a_reload_and_never_while_the_lock_does_not_read
origin: src/kraft/api/deps.py §collect_addon_stores

## REQ auto-update-is-opt-in

WHERE an add-on's auto-update is on, through its collection's `auto_update: true` or its own, the system SHALL update that add-on in the background after the server starts and once a day while it runs, and SHALL NOT fetch a collection none of whose installed add-ons auto-update, except to restore a missing store.
enforced-by: tests/addons/test_auto_update.py::test_who_auto_updates[addon-on], tests/addons/test_auto_update.py::test_who_auto_updates[collection-on], tests/addons/test_auto_update.py::test_who_auto_updates[neither-is-not-fetched], tests/api/test_deps.py::test_the_server_takes_an_auto_update_and_loads_it, tests/api/test_deps.py::test_a_running_server_checks_for_auto_updates_again
origin: src/kraft/addons/update.py §auto_update

## REQ auto-update-holds-what-widens-a-run

IF an auto-update is a downgrade, a move onto a pre-release, or has a review call-out about what a run can reach, THEN the system SHALL hold it for a person and keep the locked version.
enforced-by: tests/addons/test_auto_update.py::test_auto_update_holds[gate-removed], tests/addons/test_auto_update.py::test_auto_update_holds[limit-raised], tests/addons/test_auto_update.py::test_auto_update_holds[requires], tests/addons/test_auto_update.py::test_auto_update_holds[downgrade], tests/addons/test_auto_update.py::test_auto_update_holds[pre-release], tests/addons/test_auto_update.py::test_a_newer_pre_release_follows_a_pre_release
origin: src/kraft/addons/update.py §may_apply_unattended

## REQ auto-update-never-installs-or-re-installs

The system SHALL NOT auto-update an add-on that has no lock entry, to a newer commit at an unchanged version, or to a changed alias, ref or collection URL.
enforced-by: tests/addons/test_auto_update.py::test_auto_update_never[installs], tests/addons/test_auto_update.py::test_auto_update_never[re-installs], tests/addons/test_auto_update.py::test_auto_update_never[applies-a-ref-change], tests/addons/test_auto_update.py::test_auto_update_never[follows-a-new-url]
origin: src/kraft/addons/update.py §auto_update

## REQ failed-auto-update-keeps-the-locked-version

IF an auto-update fails or is held, THEN the system SHALL keep serving the locked version and SHALL report the outcome in `/health` and `kraft admin doctor` without marking health degraded, until a person applies the update or uninstalls the add-on.
enforced-by: tests/addons/test_auto_update.py::test_a_failed_auto_update_keeps_the_lock_and_records_why, tests/api/test_deps.py::test_a_held_or_failed_auto_update_is_reported_and_not_degraded, tests/test_doctor_addons.py::test_the_addon_updates_row_warns_and_never_fails[held], tests/test_doctor_addons.py::test_the_addon_updates_row_warns_and_never_fails[failed-auth], tests/addons/test_auto_update.py::test_a_person_taking_a_held_update_clears_its_warning, tests/cli/test_addon.py::test_uninstall_forgets_the_addons_last_auto_update
origin: src/kraft/addons/update.py §auto_update

## REQ a-major-update-names-the-addons-it-drops

WHEN `kraft admin update` would install a release whose major leaves a loaded add-on's `requires.kraft` unmet, the system SHALL list those add-ons and ask before installing.
enforced-by: tests/cli/test_admin_update.py::test_a_major_update_lists_the_addons_it_would_drop[next-major-asks], tests/cli/test_admin_update.py::test_a_major_update_lists_the_addons_it_would_drop[next-major-accepted], tests/cli/test_admin_update.py::test_a_major_update_lists_the_addons_it_would_drop[same-major-says-nothing]
origin: src/kraft/cli/admin.py §_confirm_dropped_addons

## REQ addon-content-is-read-only

IF a write would save an add-on's component or chain, THEN the system SHALL answer 409 and name the add-on.
enforced-by: tests/api/test_addon_content.py::test_saving_an_addon_entry_answers_409[put-chain], tests/api/test_addon_content.py::test_saving_an_addon_entry_answers_409[chains-draft], tests/api/test_addon_content.py::test_saving_an_addon_entry_answers_409[library-op]
origin: src/kraft/templates/catalogue.py §read_only_message

## REQ an-addon-copy-resolves-locally

WHEN an add-on component or chain is copied into a local draft, the system SHALL carry its add-on-internal references qualified, so the copy resolves.
enforced-by: tests/api/test_addon_content.py::test_copy_from_an_addon[chain], tests/api/test_addon_content.py::test_copy_from_an_addon[component]
origin: src/kraft/drafts/ops.py §copy_component

## REQ installed-addons-are-recomputed-only-when-an-input-changes

WHEN `installed()` is called again and none of the files it reads has changed, the system SHALL return the earlier answer without re-running the load-time checks.
enforced-by: tests/addons/test_load.py::test_installed_is_answered_from_a_cache_until_an_input_changes[addons-yaml], tests/addons/test_load.py::test_installed_is_answered_from_a_cache_until_an_input_changes[addons-lock], tests/addons/test_load.py::test_installed_is_answered_from_a_cache_until_an_input_changes[harnesses-yaml], tests/addons/test_load.py::test_installed_is_answered_from_a_cache_until_an_input_changes[repos-yaml], tests/addons/test_load.py::test_installed_is_answered_from_a_cache_until_an_input_changes[policy-yaml], tests/addons/test_load.py::test_installed_is_answered_from_a_cache_until_an_input_changes[dropped-store], tests/addons/test_load.py::test_installed_is_answered_from_a_cache_until_an_input_changes[unrestored-reason], tests/addons/test_load.py::test_verify_and_an_unsaved_config_never_hit_the_cache
origin: src/kraft/addons/load.py §installed

## REQ a-pending-addons-yaml-edit-is-linted-once

WHILE an `addons.yaml` edit is pending and nothing it reads changes, the system SHALL lint it once, not on each pending check.
enforced-by: tests/api/test_config_check.py::test_a_pending_addons_yaml_edit_is_linted_once
origin: src/kraft/api/config_check.py §_check_addons

## REQ shutdown-stops-every-addon-background-task

WHEN the server shuts down, the system SHALL cancel every add-on background task and stop its worker thread before the next add-on write.
enforced-by: tests/api/test_deps_addon_tasks.py::test_shutdown_cancels_every_addon_task_and_stops_the_thread, tests/api/test_deps_addon_tasks.py::test_shutdown_names_a_thread_that_outlives_the_grace, tests/addons/test_load.py::test_restore_missing_stops_between_addons_once_cancelled, tests/addons/test_update_hardening.py::test_auto_update_stops_between_collections_once_cancelled
origin: src/kraft/api/startup.py §lifespan

## REQ a-launch-stops-only-for-an-addon-it-reads

IF a pinned add-on's store cannot be read, THEN the system SHALL stop only a launch whose task selects that add-on's skill or profile.
enforced-by: tests/addons/test_pins.py::test_a_missing_pin_the_task_does_not_read_does_not_stop_it[skill-from-it-stops], tests/addons/test_pins.py::test_a_missing_pin_the_task_does_not_read_does_not_stop_it[profile-from-it-stops], tests/addons/test_pins.py::test_a_missing_pin_the_task_does_not_read_does_not_stop_it[fallback-to-it-stops], tests/addons/test_pins.py::test_a_missing_pin_the_task_does_not_read_does_not_stop_it[unrelated-task-launches]
origin: src/kraft/adapters/agent.py §resolve_agent_task

## REQ a-profile-fallback-pins-the-addon-it-names

WHEN a chain selects a local agent profile whose `fallback` names an add-on profile, the system SHALL pin that add-on.
enforced-by: tests/addons/test_pins.py::test_a_local_profiles_fallback_pins_the_addon_it_names
origin: src/kraft/templates/library.py §_pins

## REQ a-launch-restores-its-pins-before-reading-profiles

WHEN an item's launch needs a pinned store that is gone, the system SHALL restore it before reading the harness profile table, at dispatch, gate review, escalation and review reply.
enforced-by: tests/executor/test_dispatch_pins.py::test_the_fallback_table_is_read_after_the_pins_are_restored, tests/executor/test_gates_autoreview.py::test_the_profile_fallback_check_reads_restored_pins, tests/test_escalate.py::test_an_escalation_restores_the_pinned_store, tests/test_review_reply.py::test_a_review_reply_restores_the_pinned_store
origin: src/kraft/executor/dispatch.py §restore_pins

## REQ a-directory-collection-catalogue-is-read-bounded

IF a directory collection's `collection.json` is a symlink, a FIFO or over the JSON cap, THEN the system SHALL refuse the collection.
enforced-by: tests/addons/test_update_hardening.py::test_a_directory_collections_catalogue_is_read_safely[fifo], tests/addons/test_update_hardening.py::test_a_directory_collections_catalogue_is_read_safely[symlink], tests/addons/test_update_hardening.py::test_a_directory_collections_catalogue_is_read_safely[oversized]
origin: src/kraft/addons/update.py §_catalogue
