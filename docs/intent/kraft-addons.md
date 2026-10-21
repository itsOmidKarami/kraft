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

## REQ an-addon-has-at-most-one-parent

IF an add-on's `requires.addons` names more than one add-on, or one with the add-on's own name, THEN the system SHALL refuse the add-on.
enforced-by: tests/addons/test_manifest.py::test_requires_addons[two-parents], tests/addons/test_manifest.py::test_requires_addons[own-name]
origin: src/kraft/addons/manifest.py §AddonManifest

## REQ a-parent-tree-has-no-cycle-and-bounded-depth

IF an add-on's ancestry comes back to an add-on already in it, or holds more than 8 add-ons, root included, THEN the system SHALL leave the add-on out, naming the cycle or the depth.
enforced-by: tests/addons/test_inherit.py::test_ancestry[cycle], tests/addons/test_inherit.py::test_ancestry[depth-9], tests/addons/test_inherit.py::test_ancestry[depth-8]
origin: src/kraft/addons/inherit.py §ancestry

## REQ an-ancestor-name-is-not-a-descendants

IF an add-on's ancestor has the name of the add-on or of another add-on between them, THEN the system SHALL leave the add-on out, naming the ancestor.
enforced-by: tests/addons/test_inherit.py::test_ancestry[same-name-ancestor]
origin: src/kraft/addons/inherit.py §ancestry

## REQ every-child-accepts-its-parents-version

IF an add-on's parent is locked at a version outside the range its `requires.addons` gives, THEN the system SHALL leave the add-on out, naming the version and the range; IF installing an add-on would put it outside its parent's range, THEN the system SHALL refuse that install, naming the version and the range; and IF updating an add-on would put it outside its parent's range, or its parent outside an installed child's range, THEN the system SHALL hold that update rather than refuse it, naming the version and the range.
enforced-by: tests/addons/test_inherit.py::test_ancestry[out-of-range], tests/addons/test_inherit.py::test_ancestry[no-range-is-not-checked], tests/addons/test_update_inheritance.py::test_a_child_is_judged_with_its_ancestors[child-out-of-range], tests/addons/test_update_inheritance.py::test_a_child_is_judged_with_its_ancestors[parent-update-leaves-child-out-of-range], tests/addons/test_update_tree.py::test_a_tree_moves_as_a_set[child-held]
origin: src/kraft/addons/inherit.py §ancestry

## REQ a-child-of-a-left-out-parent-is-left-out

IF an add-on's parent is not installed as an add-on, or is not loaded, THEN the system SHALL leave the add-on out, with `its parent <id> is not loaded: <the parent's reason>` when the parent is installed, so a left-out add-on takes every descendant with it.
enforced-by: tests/addons/test_inherit.py::test_ancestry[missing-parent], tests/addons/test_inherit.py::test_ancestry[left-out-parent], tests/addons/test_inherit.py::test_ancestry[repo-library-not-a-parent], tests/addons/test_inherit.py::test_cascade[left-out-root], tests/addons/test_inherit.py::test_cascade[below-a-cycle], tests/addons/test_load.py::test_installed_with_inheritance[parent-disabled], tests/addons/test_load.py::test_installed_with_inheritance[parent-store-edited], tests/addons/test_load.py::test_installed_with_inheritance[parent-library-edited], tests/addons/test_load.py::test_installed_with_inheritance[union-requirements], tests/addons/test_load.py::test_installed_with_inheritance[parent-left-out-after-its-content-check]
origin: src/kraft/addons/inherit.py §cascade

## REQ a-child-is-judged-with-its-ancestors

WHEN the system decides whether an add-on with a parent loads, whether to install or update it, what its review shows, or whether a `policy.yaml` edit would leave it out, the system SHALL read its library with its ancestors' in place and check its limits against `maxima:` with them, so what it inherits and extends from them is resolved.
enforced-by: tests/addons/test_load.py::test_installed_with_inheritance[child-library-uses-parent], tests/addons/test_load.py::test_installed_with_inheritance[child-limits-through-parent], tests/api/test_config_check.py::test_an_edit_that_drops_an_addon_is_reported[maxima-lowered-child-through-parent], tests/addons/test_update_inheritance.py::test_a_child_is_judged_with_its_ancestors[child-installs-on-installed-parent], tests/addons/test_update_inheritance.py::test_a_childs_review_shows_its_chain_changes[child-drops-its-gate]
origin: src/kraft/addons/load.py §_content_problem

## REQ a-childs-requirements-include-its-ancestors

IF a harness or agent profile that an add-on or any of its ancestors requires is not defined in `harnesses.yaml`, THEN the system SHALL leave the add-on out, naming what is missing.
enforced-by: tests/addons/test_inherit.py::test_requirements[from-stores], tests/addons/test_inherit.py::test_requirements[from-manifests]
origin: src/kraft/addons/inherit.py §requirements

## REQ a-child-inherits-its-parents-library

WHERE an add-on names a parent, the system SHALL serve under the add-on's namespace every chain, task, step, node, steering profile and skill of its parent's merged library that it does not override, so a grandchild inherits its grandparent's entries through its parent, and SHALL serve each skill it inherits under the child's namespace to a reference from outside the tree, read from the store of the ancestor that defines it; a skill the child removes is not found there.
enforced-by: tests/templates/test_library_inheritance.py::test_inherited_library[inherited-as-is], tests/templates/test_library_inheritance.py::test_inherited_library[skill-inherited], tests/templates/test_library_inheritance.py::test_inherited_library[steering-inherited], tests/templates/test_library_inheritance.py::test_an_inherited_skill_is_named_under_the_child[inherited-skill], tests/templates/test_library_inheritance.py::test_an_inherited_skill_is_named_under_the_child[removed-skill], tests/templates/test_library_inheritance.py::test_an_inherited_skill_is_named_under_the_child[removed-then-own], tests/addons/test_pins_tree.py::test_a_launch_reads_an_inherited_skill_under_the_childs_name
origin: src/kraft/templates/library.py §TemplateLibrary._add_inherited

## REQ the-parents-name-means-the-inherited-layer

WHEN a child add-on's `extends`, `steering`, `skill` or qualified `profile` names its parent by the parent's name, the system SHALL read the entry of the parent's merged library as the child inherits it, whatever namespace the instance gives the parent.
enforced-by: tests/templates/test_library_inheritance.py::test_inherited_library[extend-inherited-component], tests/templates/test_library_inheritance.py::test_inherited_library[extend-inherited-chain], tests/templates/test_library_inheritance.py::test_inherited_library[alias-parent], tests/templates/test_library_inheritance.py::test_inherited_library[skill-by-parent-name], tests/templates/test_library_inheritance.py::test_inherited_library[steering-by-parent-name], tests/templates/test_library_inheritance.py::test_inherited_library[profile-by-parent-name]
origin: src/kraft/addons/inherit.py §requalify

## REQ an-inherited-entry-reads-the-childs-overrides

WHEN an entry a child add-on inherits references another entry by its bare or own name, the system SHALL resolve that reference in the child's merged library, through `extends` too, so the inherited entry runs the child's override.
enforced-by: tests/templates/test_library_inheritance.py::test_inherited_library[late-binding-through-extends], tests/templates/test_library_inheritance.py::test_inherited_library[late-binding-inherited-chain]
origin: src/kraft/addons/inherit.py §requalify

## REQ a-childs-entry-overrides-its-inherited-one

WHEN a child add-on declares a chain, library entry or skill with the name of one it inherits, in the same section, the system SHALL serve the child's in place of the inherited one under the child's namespace.
enforced-by: tests/templates/test_library_inheritance.py::test_inherited_library[override-replaces], tests/templates/test_library_inheritance.py::test_inherited_library[skill-overridden]
origin: src/kraft/templates/library.py §TemplateLibrary._add_inherited

## REQ a-child-removes-only-what-it-inherits

WHERE a child add-on's `library.yaml` has a `remove:` naming entries of its inherited layer by section, the system SHALL serve none of them under that add-on's namespace or any descendant's while its ancestors keep serving their own, SHALL record a name the parent does not have as stale rather than refuse the add-on at load but refuse it at install and update, and SHALL refuse a `remove:` section or value it does not understand.
enforced-by: tests/addons/test_update_inheritance.py::test_a_child_is_judged_with_its_ancestors[remove-typo], tests/templates/test_library_inheritance.py::test_remove[remove-chain], tests/templates/test_library_inheritance.py::test_remove[remove-task], tests/templates/test_library_inheritance.py::test_remove[remove-reaches-descendants], tests/templates/test_library_inheritance.py::test_remove[remove-skill], tests/templates/test_library_inheritance.py::test_remove[remove-steering], tests/templates/test_library_inheritance.py::test_remove[stale-name], tests/templates/test_library_inheritance.py::test_remove_is_refused[unknown-section], tests/templates/test_library_inheritance.py::test_remove_is_refused[not-a-list]
origin: src/kraft/addons/inherit.py §removals

## REQ a-removal-that-breaks-a-reference-is-refused

IF a child add-on removes an entry that something remaining in its merged library still uses, THEN the system SHALL report the entry that uses it as not resolving, naming both, and SHALL refuse to install or update to that add-on.
enforced-by: tests/templates/test_library_inheritance.py::test_remove[remove-breaks-ref], tests/addons/test_update_inheritance.py::test_a_child_is_judged_with_its_ancestors[remove-breaks-a-reference]
origin: src/kraft/templates/library.py §TemplateLibrary._add_inherited

## REQ remove-is-refused-outside-a-child

IF a `remove:` key is in the local `library.yaml`, in a repo library, or in an add-on that names no parent, the root of a child's ancestry included, THEN the system SHALL refuse it with "`remove:` is for an add-on that builds on another", leaving that add-on out at load instead of ignoring the key.
enforced-by: tests/templates/test_library_inheritance.py::test_remove_is_refused[local-library], tests/templates/test_library_inheritance.py::test_remove_is_refused[root-of-tree], tests/templates/test_library_inheritance.py::test_a_root_addon_with_remove_is_left_out, tests/addons/test_repo_library.py::test_a_repo_library_is_refused_for_what_an_addon_is[repo-library-remove]
origin: src/kraft/templates/library.py §_refuse_remove

## REQ a-hidden-layer-is-reached-only-through-the-parent

IF a local library, a chain file or an add-on's file names a child add-on's inherited layer directly rather than by the parent's name, THEN the system SHALL refuse the reference.
enforced-by: tests/templates/test_library_inheritance.py::test_a_hidden_level_is_not_reachable_by_name[local-chain], tests/templates/test_library_inheritance.py::test_a_hidden_level_is_not_reachable_by_name[local-library], tests/templates/test_library_inheritance.py::test_a_hidden_level_is_not_reachable_by_name[addon-library]
origin: src/kraft/templates/library.py §_refuse_layer_reference

## REQ a-pinned-child-builds-with-its-parent

WHEN a work item rebuilds its library from its pins and a pinned add-on names a parent, the system SHALL build that add-on as a child of the parent the instance has installed.
enforced-by: tests/addons/test_pins.py::test_a_pinned_child_builds_with_its_parent[child-pin-resolves-with-current-ancestors]
origin: src/kraft/addons/load.py §for_item

## REQ addon-content-names-its-author

The system SHALL know, for every chain and library entry a child add-on serves, the add-on whose file it was read from, telling apart a chain and a library entry of one name, and SHALL NOT show a hidden inherited layer's name in any listing, lint message, error or API payload.
enforced-by: tests/templates/test_library_inheritance.py::test_read_only_names_the_author, tests/templates/test_library_inheritance.py::test_layers_never_leak[lint], tests/templates/test_library_inheritance.py::test_layers_never_leak[provenance-parent], tests/templates/test_library_inheritance.py::test_layers_never_leak[chains-extends], tests/templates/test_library_inheritance.py::test_layers_never_leak[catalogue]
origin: src/kraft/templates/library.py §TemplateLibrary.authored_by

## REQ unknown-manifest-keys-are-ignored

The system SHALL ignore a top-level `collection.json` or `addon.json` key it does not read, and SHALL refuse an unknown key inside `requires`.
enforced-by: tests/addons/test_manifest.py::test_unknown_keys_are_ignored[collection], tests/addons/test_manifest.py::test_unknown_keys_are_ignored[addon], tests/addons/test_manifest.py::test_unknown_keys_are_ignored[claude-code-keys], tests/addons/test_manifest.py::test_an_addon_is_refused[unknown-requires-key]
origin: src/kraft/addons/manifest.py §AddonManifest

## REQ validate-warns-about-unknown-keys

WHEN `validate` reads a manifest key Kraft does not read, the system SHALL list it as a warning.
enforced-by: tests/cli/test_addon_validate.py::test_validate[unknown-key-warning]
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

WHEN an author validates a local collection or add-on, the system SHALL run the same extraction checks, add-on checks and whole-library lint as an install, judging a child add-on together with its ancestors, SHALL report each problem with its file and key, and SHALL NOT change `addons.yaml`, `addons.lock` or the store.
enforced-by: tests/cli/test_addon_validate.py::test_validate[collection], tests/cli/test_addon_validate.py::test_validate[single-addon], tests/cli/test_addon_validate.py::test_validate[lint-failure], tests/cli/test_addon_validate.py::test_validate[wrong-kraft-major], tests/cli/test_addon_validate.py::test_validate[no-home], tests/cli/test_addon_validate.py::test_validate[refused-content], tests/cli/test_addon_validate.py::test_validate_writes_nothing_and_prints_json, tests/cli/test_addon.py::test_validate_against_a_kraft_home[same-id-is-not-a-clash], tests/cli/test_addon.py::test_validate_against_a_kraft_home[requires-unmet], tests/cli/test_addon.py::test_validate_against_a_kraft_home[breaks-a-chain], tests/cli/test_addon_validate.py::test_validate[child-broken-against-parent], tests/cli/test_addon_validate.py::test_validate[child-removes-what-it-inherits], tests/cli/test_addon_validate.py::test_validate_against_an_instance_writes_nothing
origin: src/kraft/cli/addon.py §validate

## REQ validate-resolves-a-parent-from-path-or-with

WHEN an author validates a child add-on, the system SHALL resolve each of its ancestors from, in order, the validated collection, each `--with` directory, the instance's installed add-on and the current version in the instance's collection, and IF an ancestor is found in none of them, or the tree is a cycle or too deep, THEN the system SHALL exit 1 saying so, alongside every problem the checks that need only the child's own files find.
enforced-by: tests/cli/test_addon_validate.py::test_validate[child-with-parent-in-path], tests/cli/test_addon_validate.py::test_validate[child-with-grandparent-in-path], tests/cli/test_addon_validate.py::test_validate[child-with-parent-via-with], tests/cli/test_addon_validate.py::test_validate[child-parent-not-found], tests/cli/test_addon_validate.py::test_validate[child-against-instance], tests/cli/test_addon_validate.py::test_validate[child-against-instance-collection], tests/cli/test_addon_validate.py::test_validate_writes_nothing_and_prints_json, tests/cli/test_addon_validate.py::test_validate[child-parent-not-found-and-refused-content], tests/cli/test_addon_validate.py::test_validate[child-in-a-cycle], tests/cli/test_addon_validate.py::test_validate[with-dir-holds-neither], tests/cli/test_addon_validate.py::test_validate[with-dir-unreadable-before-the-parent]
origin: src/kraft/cli/addon.py §_ancestors_for

## REQ addons-load-only-from-the-lock

The system SHALL load an add-on only when it has both an enabled `addons.yaml` entry and a lock entry, SHALL load it from the store the lock names, and SHALL NOT fetch a collection when it loads the library.
enforced-by: tests/addons/test_load.py::test_load_reads_the_lock_not_the_ref, tests/addons/test_load.py::test_what_loads[listed-not-locked], tests/addons/test_load.py::test_what_loads[locked-not-listed], tests/addons/test_load.py::test_what_loads[disabled]
origin: src/kraft/addons/load.py §installed

## REQ a-broken-addon-affects-only-itself

IF an add-on fails a load-time check, THEN the system SHALL leave only that add-on and its descendants out, report it under `invalid_templates`, and load the rest of the library.
enforced-by: tests/addons/test_inherit.py::test_cascade[left-out-root], tests/addons/test_load.py::test_a_broken_addon_library_affects_only_that_addon, tests/addons/test_load.py::test_load_time_checks[requires-harness-removed], tests/addons/test_load.py::test_load_time_checks[repo-id-taken], tests/api/test_deps.py::test_health_names_an_addon_that_did_not_load[store-deleted], tests/addons/test_load.py::test_a_limit_above_a_lowered_maximum_leaves_the_addon_out
origin: src/kraft/addons/load.py §installed

## REQ a-hand-alias-or-ref-change-waits-for-update

WHILE an add-on's `addons.yaml` alias or its collection's `ref` differs from the lock's, the system SHALL keep loading the add-on under its locked namespace and commit until an update applies the change.
enforced-by: tests/addons/test_load.py::test_what_loads[alias-change-pending], tests/addons/test_update.py::test_a_ref_change_is_reviewed_whatever_the_version
origin: src/kraft/addons/load.py §installed

## REQ an-addon-whose-digest-does-not-match-is-not-loaded

IF an add-on's extracted files do not match the lock's digest, THEN the system SHALL leave that add-on out and report it under `invalid_templates`.
enforced-by: tests/addons/test_load.py::test_a_digest_mismatch_leaves_the_addon_out[emptied], tests/addons/test_load.py::test_a_digest_mismatch_leaves_the_addon_out[a-link-to-a-copy], tests/api/test_deps.py::test_health_names_an_addon_that_did_not_load[store-edited]
origin: src/kraft/addons/load.py §installed

## REQ an-edited-store-file-is-never-read

IF a file the system reads from an add-on's or a repo library's store after the store was written does not match the digest the store was written with, THEN the system SHALL refuse the file, naming it, instead of reading its text.
enforced-by: tests/addons/test_fetch.py::test_a_store_file_is_read_only_if_it_matches_its_digest[edited-file-is-refused], tests/addons/test_fetch.py::test_a_store_file_is_read_only_if_it_matches_its_digest[edited-digest-manifest-is-refused], tests/addons/test_fetch.py::test_a_store_file_is_read_only_if_it_matches_its_digest[unlisted-file-is-refused], tests/test_skill.py::test_addon_skill_references[edited-store-is-refused], tests/test_skill.py::test_addon_skill_references[edited-digest-manifest-is-refused], tests/templates/test_environment.py::test_addon_profiles[edited-store-is-refused], tests/templates/test_library_addons.py::test_an_addon_error_names_the_store_file[edited-after-install], tests/addons/test_update.py::test_an_installed_copy_that_does_not_read_is_said_in_the_review[edited-but-valid], tests/addons/test_repo_library.py::test_a_change_is_reviewed_against_the_accepted_version[store-edited]
origin: src/kraft/addons/fetch.py §open_store

## REQ an-edited-store-is-replaced-not-reused

WHEN an add-on's store is written and a store with the same digest already exists, the system SHALL reuse it only if its files match the digest, and otherwise replace it with the files being written.
enforced-by: tests/addons/test_fetch.py::test_a_store_is_addressed_by_its_content[edited-store-is-rewritten], tests/addons/test_fetch.py::test_a_store_is_addressed_by_its_content[edited-digest-manifest-is-rewritten]
origin: src/kraft/addons/fetch.py §write_store

## REQ an-unloaded-addon-skill-is-never-delegated

IF a skill reference's qualifier is the namespace of an add-on in `addons.yaml` or `addons.lock` that did not load, THEN the system SHALL fail validation instead of handing the reference to the agent.
enforced-by: tests/test_skill.py::test_addon_skill_references[unloaded-addon-is-an-error], tests/addons/test_load.py::test_what_loads[disabled]
origin: src/kraft/skill.py §validate

## REQ an-addon-reaches-only-its-parent

IF an add-on's qualified `extends`, `steering`, `profile` or `skill` reference names an add-on namespace other than its own or its parent's name, the grandparent's included, THEN the system SHALL refuse the add-on, saying an add-on reaches no add-on but its parent.
enforced-by: tests/addons/test_update.py::test_an_addon_cannot_carry[reference-to-another-addon], tests/addons/test_update.py::test_a_skill_may_not_reach_into_another_kraft_addon[another-kraft-addon], tests/cli/test_addon.py::test_validate_against_a_kraft_home[dependency], tests/addons/test_update.py::test_an_addon_cannot_carry[chain-extends-another-addon], tests/addons/test_update_inheritance.py::test_an_addon_reaches_only_its_parent[parent-extends], tests/addons/test_update_inheritance.py::test_an_addon_reaches_only_its_parent[parent-steering], tests/addons/test_update_inheritance.py::test_an_addon_reaches_only_its_parent[parent-profile], tests/addons/test_update_inheritance.py::test_an_addon_reaches_only_its_parent[parent-skill], tests/addons/test_update_inheritance.py::test_an_addon_reaches_only_its_parent[other-extends], tests/addons/test_update_inheritance.py::test_an_addon_reaches_only_its_parent[other-skill], tests/addons/test_update_inheritance.py::test_a_child_is_judged_with_its_ancestors[child-installs-on-installed-parent], tests/addons/test_update_inheritance.py::test_a_child_is_judged_with_its_ancestors[qualifier-grandparent]
origin: src/kraft/addons/update.py §problems

## REQ install-refuses-a-missing-parent-collection

IF any ancestor of an add-on being installed or updated comes from a collection that is not in `addons.yaml`, THEN the system SHALL refuse it, naming that ancestor and collection and the `kraft admin addon collection add` to run, or, when the parent is disabled, the `kraft admin addon enable` to run; and an installed add-on with nothing to take SHALL be reported current, with why its ancestry does not hold, never refused.
enforced-by: tests/addons/test_update_inheritance.py::test_a_child_is_judged_with_its_ancestors[child-missing-parent-collection], tests/addons/test_update_pull_in.py::test_install_pulls_in_missing_ancestors[missing-collection], tests/addons/test_update_inheritance.py::test_a_child_is_judged_with_its_ancestors[child-of-a-disabled-parent], tests/addons/test_update_inheritance.py::test_a_child_is_judged_with_its_ancestors[current-child-of-a-disabled-parent], tests/addons/test_update_pull_in.py::test_install_pulls_in_missing_ancestors[ancestor-disabled]
origin: src/kraft/addons/update.py §_ancestors

## REQ an-addon-cannot-run-commands-or-grant-permissions

IF an add-on declares a `subprocess` task, sets a `policy:` key other than a time cap, `timeout_minutes`, `max_attempts`, `budget_usd`, `token_budget` or `deny_tools` at any scope, or names a harness not in its `requires.harnesses`, THEN the system SHALL refuse the add-on.
enforced-by: tests/addons/test_update.py::test_an_addon_cannot_carry[subprocess], tests/addons/test_update.py::test_an_addon_cannot_carry[sandbox], tests/addons/test_update.py::test_an_addon_cannot_carry[unrestricted-network], tests/addons/test_update.py::test_an_addon_cannot_carry[grants], tests/addons/test_update.py::test_an_addon_cannot_carry[allowed-tools], tests/addons/test_update.py::test_an_addon_cannot_carry[allowed-harnesses], tests/addons/test_update.py::test_an_addon_cannot_carry[escalation-harness], tests/addons/test_update.py::test_an_addon_cannot_carry[unknown-policy-key], tests/addons/test_update.py::test_an_addon_cannot_carry[gate-scope], tests/addons/test_update.py::test_an_addon_cannot_carry[judge-task], tests/addons/test_update.py::test_an_addon_cannot_carry[unlisted-harness]
origin: src/kraft/addons/update.py §check

## REQ update-shows-what-will-run-before-applying

WHEN an install or update would change an installed add-on, the system SHALL list, before it writes anything, its changes to gates, merge steps, the order of a chain's nodes, forge targets, task harnesses and models, policy limits, `requires`, its parent and range, chains worked out with its ancestors, a stale `remove:`, prompts, steering, skills, profiles and add-on skill references, the references its namespace captures, and whether it is a downgrade.
enforced-by: tests/addons/test_review.py::test_review_calls_out[gate-removed], tests/addons/test_review.py::test_review_calls_out[gate-replaced], tests/addons/test_review.py::test_review_calls_out[auto-review], tests/addons/test_review.py::test_review_calls_out[merge-step], tests/addons/test_review.py::test_review_calls_out[forge-target], tests/addons/test_review.py::test_review_calls_out[target-used-by-a-sibling-chain], tests/addons/test_review.py::test_review_calls_out[harness], tests/addons/test_review.py::test_review_calls_out[limit-raised], tests/addons/test_review.py::test_review_calls_out[requires], tests/addons/test_review.py::test_review_calls_out[parent-range], tests/addons/test_review.py::test_review_calls_out[parent-changed], tests/addons/test_review.py::test_review_calls_out[parent-gained], tests/addons/test_review.py::test_review_calls_out[parent-dropped], tests/addons/test_review.py::test_review_with_ancestors[inherited-chain-change], tests/addons/test_review.py::test_review_with_ancestors[stale-remove], tests/addons/test_review.py::test_review_calls_out[prompt], tests/addons/test_review.py::test_review_calls_out[steering], tests/addons/test_review.py::test_review_calls_out[skill], tests/addons/test_review.py::test_review_calls_out[profile], tests/addons/test_review.py::test_review_calls_out[plugin-ref], tests/addons/test_review.py::test_review_calls_out[downgrade], tests/addons/test_review.py::test_a_reordered_chain_is_reviewed[gate-after-merge], tests/addons/test_review.py::test_a_reordered_chain_is_reviewed[work-moved-past-a-gate], tests/addons/test_update.py::test_the_review_covers_the_instances_own_chains[captured-ref], tests/addons/test_update.py::test_the_review_covers_the_instances_own_chains[local-chain-through-the-addon], tests/addons/test_update_extends.py::test_the_review_shows_what_an_extending_chain_will_run, tests/addons/test_review.py::test_a_childs_chains_are_reviewed_with_its_ancestors[child-gate-added-is-reviewed], tests/addons/test_update_inheritance.py::test_a_childs_review_shows_its_chain_changes[child-drops-its-gate], tests/addons/test_update_inheritance.py::test_a_childs_review_shows_its_chain_changes[parent-update-changes-a-childs-chain], tests/addons/test_update_tree.py::test_a_parent_review_shows_each_descendants_changes[stale-remove]
origin: src/kraft/addons/review.py §review

## REQ the-review-cannot-hide-text

WHEN the system prints a review, it SHALL escape every control, bidi and format character in it.
enforced-by: tests/addons/test_review.py::test_review_escapes_what_it_prints, tests/addons/test_review.py::test_render_group[escapes]
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
enforced-by: tests/addons/test_update.py::test_a_declined_update_writes_nothing[install], tests/addons/test_update.py::test_a_declined_update_writes_nothing[update], tests/addons/test_update.py::test_a_batch_continues_past_a_refused_addon, tests/cli/test_addon.py::test_a_refused_install_writes_nothing, tests/cli/test_addon.py::test_update_check_exit_codes[update-waiting], tests/cli/test_addon.py::test_no_terminal_without_yes_declines, tests/addons/test_update_tree.py::test_a_tree_moves_as_a_set[tree-declined-writes-nothing], tests/addons/test_update_pull_in.py::test_install_pulls_in_missing_ancestors[ancestor-refused-writes-nothing], tests/addons/test_update_pull_in.py::test_install_pulls_in_missing_ancestors[pull-in-declined]
origin: src/kraft/addons/update.py §update

## REQ update-refuses-a-change-that-breaks-a-chain

IF an update's candidate add-ons would leave a currently resolving chain of the instance's own or of an add-on, a chain reference to one of those, or a profile fallback unresolved, or would leave out a descendant add-on that loads now, THEN the system SHALL refuse that add-on and name each such reference or descendant.
enforced-by: tests/addons/test_update.py::test_an_update_that_breaks_a_reference_is_refused[local-chain], tests/addons/test_update.py::test_an_update_that_breaks_a_reference_is_refused[repos-default-chain], tests/addons/test_update.py::test_an_update_that_breaks_a_reference_is_refused[intake-schedule], tests/addons/test_update.py::test_a_captured_reference_the_addon_does_not_provide_is_refused, tests/addons/test_update.py::test_a_broken_profile_pairing_is_refused, tests/addons/test_update_extends.py::test_an_update_that_breaks_an_extending_chain_is_refused[removed-node-gone], tests/addons/test_update_extends.py::test_an_update_that_breaks_an_extending_chain_is_refused[anchor-gone], tests/addons/test_update_extends.py::test_an_update_that_breaks_an_extending_chain_is_refused[override-path-gone], tests/addons/test_update_extends.py::test_an_update_that_breaks_an_extending_chain_is_refused[added-id-taken], tests/addons/test_update_inheritance.py::test_a_child_is_judged_with_its_ancestors[parent-update-breaks-child-chain], tests/addons/test_update_inheritance.py::test_a_child_is_judged_with_its_ancestors[parent-update-drops-a-descendant], tests/addons/test_update_tree.py::test_a_tree_moves_as_a_set[tree-break-refuses-every-member], tests/addons/test_update_tree.py::test_update_takes_waiting_relatives[broken-needed-relative-refuses], tests/addons/test_update_tree.py::test_update_takes_waiting_relatives[broken-needed-relative-own-chain-refuses], tests/addons/test_update_tree.py::test_update_takes_waiting_relatives[named-child-stale-remove-after-relative-held]
origin: src/kraft/addons/update.py §breaks

## REQ an-addon-lists-no-addons

IF an add-on's `library.yaml` has an `addons:` key, THEN the system SHALL refuse the add-on.
enforced-by: tests/addons/test_repo_library_extends.py::test_an_addon_lists_no_addons
origin: src/kraft/addons/update.py §_listed_addons

## REQ a-broken-repo-library-chain-holds-an-update

IF an update would leave a currently resolving repo-library chain unresolved, THEN the system SHALL put it in that update's review as a Reach line and SHALL NOT refuse the update for it or for a reference to it.
enforced-by: tests/addons/test_repo_library_extends.py::test_an_update_that_breaks_a_repo_library_chain_holds_and_never_refuses[by-hand-applies], tests/addons/test_repo_library_extends.py::test_an_update_that_breaks_a_repo_library_chain_holds_and_never_refuses[auto-update-holds], tests/addons/test_repo_library_extends.py::test_an_update_that_breaks_a_repo_library_chain_holds_and_never_refuses[named-by-repos-yaml], tests/addons/test_repo_library_extends.py::test_an_update_that_breaks_a_repo_library_chain_holds_and_never_refuses[then-the-library-changes], tests/cli/test_addon.py::test_validate_against_a_kraft_home[breaks-a-repo-library-chain]
origin: src/kraft/addons/update.py §held_breaks

## REQ the-review-covers-repo-library-chains

WHEN an add-on update, or a change of a repo library, alters what a repo-library chain that extends an add-on chain runs, the system SHALL list each change in the review, as Reach or content by the rules for the instance's own chains.
enforced-by: tests/addons/test_repo_library_extends.py::test_the_review_shows_what_a_repo_library_chain_will_run[limit-raised-is-reach], tests/addons/test_repo_library_extends.py::test_the_review_shows_what_a_repo_library_chain_will_run[text-is-content], tests/addons/test_repo_library_extends.py::test_a_change_of_the_library_shows_what_it_builds_on[its-extending-chain]
origin: src/kraft/addons/review.py §local_changes

## REQ addon-rules-govern-a-change-through-the-addon

WHEN an add-on auto-update changes a repo-library chain only in what the add-on's review counts as content, a change of shape included, the system SHALL apply it without a person.
enforced-by: tests/addons/test_repo_library_extends.py::test_an_addon_change_reaches_a_repo_library_chain_by_the_addons_rules[text], tests/addons/test_repo_library_extends.py::test_an_addon_change_reaches_a_repo_library_chain_by_the_addons_rules[a-change-of-shape]
origin: src/kraft/addons/update.py §auto_update

## REQ one-addon-change-at-a-time

WHILE one process writes add-on state, the system SHALL make a second writer wait, then fail, and SHALL write nothing IF the add-on files changed since the review was built.
enforced-by: tests/addons/test_update.py::test_a_second_writer_waits_then_fails, tests/addons/test_update.py::test_a_lock_changed_during_review_writes_nothing
origin: src/kraft/addons/update.py §write_lock

## REQ update-check-exit-codes

WHEN `update --check` finishes, the system SHALL exit 0 when every add-on is current, 1 when an update is waiting or held by a range, 2 when an update would be refused (a relative held because its own new release breaks a chain included), and 3 when it could not check.
enforced-by: tests/cli/test_addon.py::test_update_check_exit_codes[up-to-date], tests/cli/test_addon.py::test_update_check_exit_codes[update-waiting], tests/cli/test_addon.py::test_update_check_exit_codes[refused], tests/cli/test_addon.py::test_update_check_exit_codes[cannot-check], tests/cli/test_addon.py::test_update_check_exit_codes[held-by-range], tests/cli/test_addon.py::test_update_check_exit_codes[held-beside-refused], tests/cli/test_addon.py::test_update_check_exit_codes[broken-relative-check-exits-2]
origin: src/kraft/cli/addon.py §_cmd_update

## REQ removing-a-referenced-addon-is-refused

IF local configuration (`library.yaml`, `chains/`, `repos.yaml`, `intake.yaml`, `policy.yaml` triggers or `harnesses.yaml` fallbacks) references the namespace of an add-on being uninstalled or disabled, an accepted repo library lists it under `addons:`, or an installed, enabled add-on names it as its parent, THEN the system SHALL refuse and list each reference.
enforced-by: tests/cli/test_addon.py::test_removing_a_referenced_addon_is_refused[uninstall], tests/cli/test_addon.py::test_removing_a_referenced_addon_is_refused[disable], tests/cli/test_addon.py::test_removing_a_referenced_addon_is_refused[child-uninstall], tests/cli/test_addon.py::test_removing_a_referenced_addon_is_refused[child-disable], tests/cli/test_addon.py::test_removing_an_addon_a_chain_extends_is_refused, tests/cli/test_addon.py::test_removing_an_addon_a_repo_library_builds_on_is_refused[uninstall], tests/cli/test_addon.py::test_removing_an_addon_a_repo_library_builds_on_is_refused[disable], tests/api/test_config_check.py::test_an_addons_yaml_edit_is_checked[a-repo-library-builds-on-it]
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

IF a hand edit of `addons.yaml` would leave a currently resolving chain or reference unresolved, or leave out a loaded add-on whose own entry it keeps enabled, THEN the system SHALL report it on the pending reload with the reason, naming each descendant it leaves out, and SHALL list a changed `addons.yaml` or `addons.lock` as a pending reload.
enforced-by: tests/api/test_config_check.py::test_an_addons_yaml_edit_is_checked[breaks-a-chain], tests/api/test_config_check.py::test_an_addons_yaml_edit_is_checked[captures-a-reference], tests/api/test_config_check.py::test_an_addons_yaml_edit_is_checked[names-each-descendant-left-out], tests/api/test_deps.py::test_an_addon_reload_leaves_the_operators_pending_edits_pending
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
enforced-by: tests/addons/test_pins.py::test_a_resolved_chain_pins_the_addons_it_reads[local-chain], tests/addons/test_pins.py::test_a_resolved_chain_pins_the_addons_it_reads[addon-chain], tests/addons/test_pins.py::test_a_rebuilt_snapshot_keeps_the_items_pins[retry-override], tests/addons/test_pins.py::test_a_rebuilt_snapshot_keeps_the_items_pins[chain-revision], tests/addons/test_pins.py::test_a_launch_reads_the_version_the_item_started_with, tests/addons/test_pins.py::test_a_pinned_version_that_is_gone_is_refused_not_delegated, tests/addons/test_pins.py::test_every_launch_is_handed_the_items_pins, tests/addons/test_pins.py::test_a_revision_resolves_against_pinned_addons[through-a-pinned-addon], tests/addons/test_pins.py::test_a_revision_resolves_against_pinned_addons[through-an-addon-not-touched-yet], tests/addons/test_pins.py::test_a_chain_through_a_repo_library_pins_its_commit_and_is_restored, tests/addons/test_pins_tree.py::test_an_item_keeps_its_tree_across_a_parent_update[launch-reads-pinned-ancestor], tests/addons/test_pins_tree.py::test_a_pinned_addon_wins_over_a_live_one_of_its_id
origin: src/kraft/adapters/agent.py §resolve_agent_task

## REQ a-revision-can-add-an-addon-component

WHEN a chain revision or an item draft adds a node, the system SHALL accept `<namespace>:<name>` in the node's and its tasks' `extends`, SHALL resolve it against the add-on versions the item pinned, pinning an add-on it had not read at the lock's version, with its ancestors, only when its locked ancestors are the versions the item pinned, SHALL keep the node's, steps' and tasks' own ids bare, and SHALL refuse a component of an add-on that is not loaded for the item. An add-on the item pinned stays loaded for it at that version after it is disabled, as at launch, and stays pinned when its store is gone, for the launch to restore; one it had not pinned is not loaded once disabled.
enforced-by: tests/addons/test_pins.py::test_a_revision_resolves_against_pinned_addons[added-node-extends-an-addon], tests/addons/test_pins.py::test_a_revision_resolves_against_pinned_addons[added-task-extends-an-addon], tests/addons/test_pins.py::test_a_revision_resolves_against_pinned_addons[added-task-extends-an-addon-not-touched-yet], tests/addons/test_pins.py::test_a_revision_cannot_add_from_an_addon_that_is_not_loaded[disabled], tests/addons/test_pins.py::test_a_revision_cannot_add_from_an_addon_that_is_not_loaded[pinned-version-gone], tests/api/test_addon_content.py::test_an_item_draft_adds_an_addons_node, tests/templates/test_revision.py::test_anything_but_one_strict_change_set_is_refused[added-node-with-a-qualified-id], tests/templates/test_revision.py::test_anything_but_one_strict_change_set_is_refused[added-task-with-a-qualified-id], tests/templates/test_revision.py::test_anything_but_one_strict_change_set_is_refused[added-step-with-a-qualified-id], tests/addons/test_pins.py::test_a_revision_resolves_against_pinned_addons[pinned-addon-disabled-since], tests/addons/test_pins_tree.py::test_a_revision_never_mixes_ancestor_versions[ancestor-unchanged], tests/addons/test_pins_tree.py::test_a_revision_never_mixes_ancestor_versions[ancestor-moved], tests/addons/test_pins_tree.py::test_a_revision_reading_a_left_out_addon_is_refused_unless_left_out_on_purpose[pinned-store-gone]
origin: src/kraft/templates/revision.py §AddedNode

## REQ a-missing-store-is-restored-from-its-commit

WHEN a store that the lock or a work item's pin names is missing, the system SHALL restore it from the recorded commit, SHALL load it only if its tree and digest match, and SHALL restore a work item's pinned store before a launch reads it.
enforced-by: tests/addons/test_load.py::test_a_missing_store_is_restored_from_the_locked_commit[mirror-has-it], tests/addons/test_load.py::test_a_missing_store_is_restored_from_the_locked_commit[fetched-by-its-id], tests/addons/test_pins.py::test_a_launch_restores_the_pinned_store_it_needs, tests/api/test_deps.py::test_a_reload_restores_a_store_the_lock_names_and_this_machine_lacks, tests/api/test_deps.py::test_intake_on_an_addon_whose_store_is_away_says_retry_not_unknown, tests/addons/test_load.py::test_a_missing_repo_library_store_is_restored_from_the_checkout, tests/addons/test_pins.py::test_a_chain_through_a_repo_library_pins_its_commit_and_is_restored, tests/addons/test_pins_tree.py::test_an_item_keeps_its_tree_across_a_parent_update[restore-an-ancestor]
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

WHERE an add-on's auto-update is on, through its collection's `auto_update: true` or its own, the system SHALL update that add-on in the background after the server starts and once a day while it runs, and SHALL NOT fetch a collection none of whose installed add-ons auto-update, except to restore a missing store; an add-on whose auto-update is off is never moved by a relative's.
enforced-by: tests/addons/test_auto_update.py::test_who_auto_updates[addon-on], tests/addons/test_auto_update.py::test_who_auto_updates[collection-on], tests/addons/test_auto_update.py::test_who_auto_updates[neither-is-not-fetched], tests/addons/test_auto_update.py::test_auto_update_takes_no_relatives, tests/api/test_deps.py::test_the_server_takes_an_auto_update_and_loads_it, tests/api/test_deps.py::test_a_running_server_checks_for_auto_updates_again
origin: src/kraft/addons/update.py §auto_update

## REQ auto-update-holds-what-widens-a-run

IF an auto-update is a downgrade, a move onto a pre-release, or has a review call-out about what a run can reach, THEN the system SHALL hold it for a person and keep the locked version.
enforced-by: tests/addons/test_auto_update.py::test_auto_update_holds[gate-removed], tests/addons/test_auto_update.py::test_auto_update_holds[limit-raised], tests/addons/test_auto_update.py::test_auto_update_holds[requires], tests/addons/test_auto_update.py::test_auto_update_holds[downgrade], tests/addons/test_auto_update.py::test_auto_update_holds[pre-release], tests/addons/test_auto_update.py::test_a_newer_pre_release_follows_a_pre_release, tests/addons/test_update_inheritance.py::test_a_childs_review_shows_its_chain_changes[parent-update-changes-a-childs-chain], tests/addons/test_auto_update.py::test_a_group_is_held_whole[relative-holds]
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

IF a write would save an add-on's component or chain, THEN the system SHALL answer 409 and name the add-on, and, for an entry a child add-on inherits, the ancestor that wrote it too.
enforced-by: tests/api/test_addon_content.py::test_saving_an_addon_entry_answers_409[put-chain], tests/api/test_addon_content.py::test_saving_an_addon_entry_answers_409[chains-draft], tests/api/test_addon_content.py::test_saving_an_addon_entry_answers_409[library-op], tests/templates/test_library_inheritance.py::test_read_only_names_the_author
origin: src/kraft/templates/catalogue.py §read_only_message

## REQ an-addon-copy-resolves-locally

WHEN an add-on component or chain is copied into a local draft, the system SHALL carry its add-on-internal references qualified, and a child add-on's references to its parent's entries as the parent's own ids, so the copy resolves.
enforced-by: tests/api/test_addon_content.py::test_copy_from_an_addon[chain], tests/api/test_addon_content.py::test_copy_from_an_addon[component], tests/api/test_addon_content.py::test_a_copy_from_a_child_addon_names_its_parent[child-chain], tests/api/test_addon_content.py::test_a_copy_from_a_child_addon_names_its_parent[child-component]
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

IF an add-on's store cannot be read, pinned or loaded and edited since, THEN the system SHALL stop only a launch whose task selects that add-on's skill or profile, or one of an add-on built on it, a `harnesses.yaml` fallback into it included.
enforced-by: tests/addons/test_pins.py::test_a_missing_pin_the_task_does_not_read_does_not_stop_it[skill-from-it-stops], tests/addons/test_pins.py::test_a_missing_pin_the_task_does_not_read_does_not_stop_it[profile-from-it-stops], tests/addons/test_pins.py::test_a_missing_pin_the_task_does_not_read_does_not_stop_it[fallback-to-it-stops], tests/addons/test_pins.py::test_a_missing_pin_the_task_does_not_read_does_not_stop_it[unrelated-task-launches], tests/addons/test_pins.py::test_a_missing_pin_the_task_does_not_read_does_not_stop_it[edited-profiles-stop-its-profile], tests/addons/test_pins.py::test_a_missing_pin_the_task_does_not_read_does_not_stop_it[edited-profiles-spare-an-unrelated-task], tests/addons/test_pins.py::test_a_missing_pin_the_task_does_not_read_does_not_stop_it[a-local-fallback-into-it-stops], tests/addons/test_pins.py::test_a_missing_pin_the_task_does_not_read_does_not_stop_it[unpinned-local-fallback-into-it-stops], tests/addons/test_pins.py::test_a_missing_pin_the_task_does_not_read_does_not_stop_it[unpinned-edited-profiles-spare-an-unrelated-task], tests/addons/test_pins_tree.py::test_an_edited_library_leaves_only_its_tree_out[ancestor-library-edited], tests/addons/test_pins_tree.py::test_an_edited_library_leaves_only_its_tree_out[pinned-ancestor-library-edited], tests/addons/test_pins_tree.py::test_an_edited_library_leaves_only_its_tree_out[ancestor-edited-read-stops], tests/addons/test_pins_tree.py::test_an_edited_library_leaves_only_its_tree_out[own-library-edited], tests/addons/test_pins_tree.py::test_an_edited_library_leaves_only_its_tree_out[own-edited-read-stops]
origin: src/kraft/adapters/agent.py §resolve_agent_task

## REQ a-profile-fallback-pins-the-addon-it-names

WHEN a chain selects a local agent profile whose `fallback` names an add-on profile, the system SHALL pin that add-on and its ancestors, and a chain revision or item draft that adds such a fallback SHALL be refused, with why, when that add-on is left out by a fault, such as its locked ancestors differing from the versions the item pinned, and the item had not pinned it already.
enforced-by: tests/addons/test_pins.py::test_a_local_profiles_fallback_pins_the_addon_it_names, tests/addons/test_pins_tree.py::test_an_item_pins_its_whole_tree[fallback-into-inherited-profile], tests/addons/test_pins_tree.py::test_a_revision_never_mixes_ancestor_versions[fallback-into-a-mixed-profile], tests/addons/test_pins_tree.py::test_a_revision_reading_a_left_out_addon_is_refused_unless_left_out_on_purpose[store-edited-since], tests/addons/test_pins_tree.py::test_a_revision_reading_a_left_out_addon_is_refused_unless_left_out_on_purpose[disabled-is-the-files-mistake], tests/addons/test_pins_tree.py::test_a_revision_reading_a_left_out_addon_is_refused_unless_left_out_on_purpose[pinned-store-gone]
origin: src/kraft/templates/library.py §_pins

## REQ a-launch-restores-its-pins-before-reading-profiles

WHEN an item's launch needs a pinned store that is gone, the system SHALL restore it before reading the harness profile table, at dispatch, gate review, escalation and review reply.
enforced-by: tests/executor/test_dispatch_pins.py::test_the_fallback_table_is_read_after_the_pins_are_restored, tests/executor/test_gates_autoreview.py::test_the_profile_fallback_check_reads_restored_pins, tests/test_escalate.py::test_an_escalation_restores_the_pinned_store, tests/test_review_reply.py::test_a_review_reply_restores_the_pinned_store
origin: src/kraft/executor/dispatch.py §restore_pins

## REQ a-directory-collection-catalogue-is-read-bounded

IF a directory collection's `collection.json` is a symlink, a FIFO or over the JSON cap, THEN the system SHALL refuse the collection.
enforced-by: tests/addons/test_update_hardening.py::test_a_directory_collections_catalogue_is_read_safely[fifo], tests/addons/test_update_hardening.py::test_a_directory_collections_catalogue_is_read_safely[symlink], tests/addons/test_update_hardening.py::test_a_directory_collections_catalogue_is_read_safely[oversized]
origin: src/kraft/addons/update.py §catalogue

## REQ a-minted-repository-id-skips-addon-namespaces

WHEN Kraft writes a repository id for an entry that has none, the system SHALL NOT choose `kraft`, the namespace of an add-on `addons.yaml` or `addons.lock` names, or a namespace an `as:` in `addons.yaml` would take.
enforced-by: tests/api/test_repos_workspaces.py::test_connecting_a_workspace_declares_it_with_its_submodules_as_members[addon-namespace], tests/api/test_repos_workspaces.py::test_connecting_a_workspace_declares_it_with_its_submodules_as_members[reserved], tests/api/test_repos_library.py::test_a_repo_library_is_reviewed_then_accepted[addon-namespace], tests/api/test_repos_library.py::test_a_repo_library_is_reviewed_then_accepted[reserved], tests/api/test_repos_library.py::test_a_repo_library_is_reviewed_then_accepted[a-pending-alias], tests/api/test_repos_library.py::test_a_repo_library_is_reviewed_then_accepted[a-disabled-addon]
origin: src/kraft/api/routes/repos.py §_repository_id

## REQ a-workspace-never-names-an-addon-namespace-id

IF connecting a repository would declare a workspace naming an entry whose `id` is `kraft` or an add-on's namespace, THEN the system SHALL refuse the connect, name the add-on, and write nothing.
enforced-by: tests/api/test_repos_workspaces.py::test_a_member_whose_id_is_an_addon_namespace_refuses_the_connect[addon-namespace], tests/api/test_repos_workspaces.py::test_a_member_whose_id_is_an_addon_namespace_refuses_the_connect[reserved]
origin: src/kraft/api/routes/repos.py §_declare_workspace

## REQ a-parent-with-children-is-not-removed

IF an add-on being uninstalled or disabled, by `kraft admin addon` or by an `addons.yaml` edit, is named as parent by an installed add-on whose entry stays enabled, THEN the system SHALL refuse with `something installed builds on it`, naming each such child, and SHALL allow disabling the child.
enforced-by: tests/cli/test_addon.py::test_removing_a_referenced_addon_is_refused[child-uninstall], tests/cli/test_addon.py::test_removing_a_referenced_addon_is_refused[child-disable], tests/api/test_config_check.py::test_an_addons_yaml_edit_is_checked[a-child-builds-on-it], tests/api/test_config_check.py::test_an_addons_yaml_edit_is_checked[parent-and-child-together]
origin: src/kraft/addons/update.py §child_dependents

## REQ a-child-is-not-enabled-without-its-parent

IF `kraft admin addon enable` names an add-on whose parent, with the entry enabled, is not installed, not loaded, or outside its range, THEN the system SHALL refuse with why, naming the parent and, when it is not installed, the install to run, and write nothing.
enforced-by: tests/cli/test_addon_inheritance.py::test_enable_a_child_without_its_parent[parent-disabled], tests/cli/test_addon_inheritance.py::test_enable_a_child_without_its_parent[parent-not-installed], tests/cli/test_addon_inheritance.py::test_enable_a_child_without_its_parent[parent-out-of-range], tests/cli/test_addon_inheritance.py::test_enable_a_child_without_its_parent[parent-loaded]
origin: src/kraft/cli/addon.py §_parent_loads

## REQ a-child-inherits-its-parents-agent-profiles

WHERE an add-on names a parent, the system SHALL serve under the add-on's namespace every agent profile of its ancestors' `profiles.yaml` that it neither defines nor removes, from the nearest ancestor that defines it, and SHALL read a `fallback:` of the child's own that names its parent as the profile of the ancestor that defines it, whatever namespace the instance gives the parent.
enforced-by: tests/templates/test_environment.py::test_inherited_addon_profiles[inherited], tests/templates/test_environment.py::test_inherited_addon_profiles[nearest-owner], tests/templates/test_environment.py::test_inherited_addon_profiles[removed], tests/templates/test_environment.py::test_inherited_addon_profiles[alias-parent], tests/templates/test_environment.py::test_inherited_addon_profiles[child-fallback-names-parent], tests/templates/test_environment.py::test_inherited_addon_profiles[child-fallback-names-aliased-parent], tests/templates/test_environment.py::test_inherited_addon_profiles[own-override], tests/templates/test_environment.py::test_inherited_addon_profiles[fallback-kept], tests/templates/test_environment.py::test_inherited_addon_profiles[child-fallback-names-itself], tests/templates/test_environment.py::test_inherited_addon_profiles[child-fallback-names-parents-own]
origin: src/kraft/templates/environment.py §HarnessProfileTable.from_mapping

## REQ an-item-pins-every-ancestor

WHEN a work item's chain pins an add-on, the system SHALL pin each of its ancestors too, record each pinned add-on's parent, and load the pinned tree parents first, reading a pinned add-on's parent from its store's manifest when its pin names none and leaving it out with why when that manifest does not read, and SHALL leave a pinned child out with its ancestor's reason when a pinned ancestor's store is gone, never building it on the lock's version of that ancestor.
enforced-by: tests/addons/test_pins_tree.py::test_an_item_pins_its_whole_tree[child-chain], tests/addons/test_pins_tree.py::test_an_item_pins_its_whole_tree[outside-extends], tests/addons/test_pins_tree.py::test_an_item_pins_its_whole_tree[outside-skill], tests/addons/test_pins_tree.py::test_an_item_pins_its_whole_tree[lock-lists-the-child-first], tests/addons/test_pins_tree.py::test_an_item_keeps_its_tree_across_a_parent_update[launch-reads-pinned-ancestor], tests/addons/test_pins_tree.py::test_an_item_keeps_its_tree_across_a_parent_update[for-item-tree-order], tests/addons/test_pins_tree.py::test_an_item_keeps_its_tree_across_a_parent_update[restore-an-ancestor], tests/addons/test_pins_tree.py::test_an_item_keeps_its_tree_across_a_parent_update[pin-without-parent], tests/addons/test_pins_tree.py::test_an_item_keeps_its_tree_across_a_parent_update[child-disabled-since], tests/addons/test_pins_tree.py::test_an_item_keeps_its_tree_across_a_parent_update[pin-parent-wins-over-its-store], tests/addons/test_pins_tree.py::test_an_item_keeps_its_tree_across_a_parent_update[pin-without-parent-manifest-tampered], tests/addons/test_pins_tree.py::test_an_item_keeps_its_tree_across_a_parent_update[launch-names-the-pinned-parent-that-is-gone]
origin: src/kraft/templates/library.py §_pins

## REQ an-item-never-mixes-ancestor-versions

WHEN a chain revision, an item draft or a launch would read an add-on the work item had not pinned, and that add-on itself (under a namespace it took since intake) or any of its locked ancestors, at any depth, differs from the version the item pinned, the system SHALL refuse it, naming both versions.
enforced-by: tests/addons/test_pins_tree.py::test_a_revision_never_mixes_ancestor_versions[ancestor-moved], tests/addons/test_pins_tree.py::test_a_revision_never_mixes_ancestor_versions[middle-ancestor-moved], tests/addons/test_pins_tree.py::test_a_revision_never_mixes_ancestor_versions[fallback-into-a-mixed-profile], tests/addons/test_pins_tree.py::test_a_revision_never_mixes_ancestor_versions[ancestor-moved-in-a-draft], tests/addons/test_pins_tree.py::test_a_revision_never_mixes_ancestor_versions[ancestor-changed-under-its-version], tests/addons/test_pins_tree.py::test_a_revision_never_mixes_ancestor_versions[ancestor-moved-to-an-alias], tests/addons/test_pins_tree.py::test_a_revision_never_mixes_ancestor_versions[root-moved-to-an-alias], tests/addons/test_pins_tree.py::test_a_revision_never_mixes_ancestor_versions[child-moved-to-an-alias], tests/addons/test_pins_tree.py::test_a_launch_never_runs_a_child_on_an_older_ancestor[launch-fallback-into-a-mixed-tree]
origin: src/kraft/addons/load.py §for_item

## REQ validate-names-a-parents-own-problems-by-its-real-path

IF `kraft admin addon validate` finds a failure in an ancestor's own files, THEN the system SHALL report it once, against the directory that ancestor was read from, and SHALL NOT name a temporary copy or report the child's served copy of it as a second failure.
enforced-by: tests/cli/test_addon_validate.py::test_validate[parent-broken-not-blamed-on-child]
origin: src/kraft/cli/addon.py §_own_problems

## REQ a-draft-names-an-inherited-entry-by-its-public-id

WHERE a draft of a chain reads through a child add-on's inherited chains, the system SHALL write and show them under the ancestor's own id (detach, the per-field sources), never a hidden level's name.
enforced-by: tests/api/test_addon_content.py::test_a_local_chain_through_a_child_addon_detaches, tests/api/test_addon_content.py::test_a_drafts_sources_name_inherited_chains_publicly
origin: src/kraft/drafts/extending.py §detach

## REQ update-applies-a-consistent-set

WHEN an update judges waiting moves, the system SHALL drop every move that breaks a range, judged with the other moves applied, repeat until none breaks, then re-offer each dropped move, parents first and then by id, keeping one whose addition to the kept set breaks no range, repeat the re-offer until it keeps nothing new, and apply only what is kept.
enforced-by: tests/addons/test_inherit.py::test_settle[cascade], tests/addons/test_inherit.py::test_settle[move-together], tests/addons/test_inherit.py::test_settle[order-independent], tests/addons/test_inherit.py::test_settle[child-keeps-old-range], tests/addons/test_inherit.py::test_settle[re-offer-second-round], tests/addons/test_inherit.py::test_settle[re-offer-parents-first], tests/addons/test_inherit.py::test_settle[re-offer-by-depth-not-id], tests/addons/test_inherit.py::test_settle[re-offer-depth-is-the-locked-one], tests/addons/test_update_tree.py::test_a_tree_moves_as_a_set[move-together], tests/addons/test_update_tree.py::test_a_tree_moves_as_a_set[child-applies-parent-held], tests/addons/test_update_reparent.py::test_a_reparenting_child_moves_with_both_trees[reparent-broken-child-refuses-the-old-parent]
origin: src/kraft/addons/inherit.py §settle

## REQ a-new-parent-name-the-child-shadows-is-reach

WHEN an ancestor's update adds a name the add-on's own files declare, the system SHALL list it under Reach, naming the ancestor.
enforced-by: tests/addons/test_review.py::test_review_with_ancestors[shadow-task], tests/addons/test_review.py::test_review_with_ancestors[shadow-skill], tests/addons/test_review.py::test_review_with_ancestors[shadow-from-grandparent], tests/addons/test_review.py::test_review_with_ancestors[already-inherited-is-no-shadow], tests/addons/test_update_tree.py::test_a_parent_review_shows_each_descendants_changes[shadow]
origin: src/kraft/addons/review.py §review

## REQ a-tree-is-reviewed-as-one

WHEN moves in one tree are applied, the system SHALL show them in one review and apply them on one accept, a child that changes parent being in one tree with its new parent's, and with its old parent's when that parent moves too; an update of an id also takes the waiting moves of its installed ancestors and descendants; when one of those relatives' moves breaks a chain that the named add-ons' moves, and the moves they need, do not, that relative's move is held and the rest apply; and what the tree changes outside its members (the instance's own chains, a held repo-library break) SHALL be said once, in its first member's part of the review.
enforced-by: tests/addons/test_update_tree.py::test_a_tree_moves_as_a_set[move-together], tests/addons/test_update_tree.py::test_a_tree_moves_as_a_set[tree-own-lines-go-to-its-first-member], tests/addons/test_update_tree.py::test_a_tree_moves_as_a_set[unrelated-keep-their-own-review], tests/addons/test_update_tree.py::test_update_takes_waiting_relatives[id-takes-descendant], tests/addons/test_update_tree.py::test_update_takes_waiting_relatives[id-takes-ancestor], tests/addons/test_update_tree.py::test_update_takes_waiting_relatives[broken-relative-held], tests/addons/test_update_reparent.py::test_a_reparenting_child_moves_with_both_trees[reparent-decline-keeps-old-parent], tests/addons/test_update_reparent.py::test_a_reparenting_child_moves_with_both_trees[reparent-both-apply], tests/addons/test_update_reparent.py::test_a_reparenting_child_moves_with_both_trees[dept-stays-its-own-group]
origin: src/kraft/addons/update.py §update

## REQ a-move-held-by-a-range-is-waiting

IF a move is dropped by a range, THEN the system SHALL report it held with that range and keep the locked version.
enforced-by: tests/addons/test_update_tree.py::test_a_tree_moves_as_a_set[parent-held], tests/addons/test_update_tree.py::test_a_tree_moves_as_a_set[child-held], tests/addons/test_auto_update.py::test_a_range_held_auto_update_waits
origin: src/kraft/addons/update.py §_settle

## REQ install-pulls-in-missing-ancestors

WHEN an install's ancestors are not installed, the system SHALL install them with it, under one review and one accept, with `--as` and `--auto-update` applying to the named add-on only.
enforced-by: tests/addons/test_update_pull_in.py::test_install_pulls_in_missing_ancestors[pulls-in-two], tests/addons/test_update_pull_in.py::test_install_pulls_in_missing_ancestors[as-names-only-the-child], tests/addons/test_update_pull_in.py::test_install_pulls_in_missing_ancestors[auto-update-only-the-child], tests/cli/test_addon.py::test_install_pulls_in_its_parent
origin: src/kraft/addons/update.py §update

## REQ a-parent-review-shows-each-descendants-changes

WHEN an ancestor moves, the system SHALL list in the review of each installed descendant's nearest moving ancestor the names that descendant newly shadows and its `remove:` entries that stop doing anything, each line naming the descendant by its id, and the chain changes it sees, each line carrying only its namespace; SHALL read the descendant's files as loading does, and when it cannot, say so in a Reach line; and SHALL hold an unattended update for a person when a descendant gains a Reach line from it.
enforced-by: tests/addons/test_update_tree.py::test_a_parent_review_shows_each_descendants_changes[shadow], tests/addons/test_update_tree.py::test_a_parent_review_shows_each_descendants_changes[stale-remove], tests/addons/test_update_tree.py::test_a_descendant_that_cannot_be_read_holds_its_parents_update[edited-file-still-shown], tests/addons/test_update_tree.py::test_a_descendant_that_cannot_be_read_holds_its_parents_update[unreadable-store], tests/addons/test_update_tree.py::test_a_descendants_lines_go_to_its_nearest_moving_ancestor, tests/addons/test_auto_update.py::test_a_descendants_reach_holds_its_parents_auto_update[descendant-shadows-a-new-name], tests/addons/test_auto_update.py::test_a_descendant_that_cannot_be_read_holds_its_parents_auto_update
origin: src/kraft/addons/update.py §_below
