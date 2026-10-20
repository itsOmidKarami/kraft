# Intent: Templates editor

The Templates screen edits a chain or the library as a draft, through ops
(`src/kraft/drafts/ops.py`), and publishes it. This file holds the editor's
requirements for chains that extend a chain; the rest of the editor predates
the tree. `enforced-by:` names the test that pins each requirement.

## REQ an-extending-chain-edit-writes-no-nodes-list

WHEN an editor op other than `detach` changes a chain that extends another, the system SHALL NOT write a `nodes` key into that chain.
enforced-by: tests/drafts/test_extending.py::test_no_op_writes_a_nodes_list[add_node], tests/drafts/test_extending.py::test_no_op_writes_a_nodes_list[remove], tests/drafts/test_extending.py::test_no_op_writes_a_nodes_list[move], tests/drafts/test_extending.py::test_no_op_writes_a_nodes_list[set_field], tests/drafts/test_extending.py::test_no_op_writes_a_nodes_list[add_task-in-added], tests/drafts/test_extending.py::test_no_op_writes_a_nodes_list[restore]
origin: src/kraft/drafts/extending.py

## REQ removing-an-inherited-node-writes-remove

WHEN the editor removes a node an extending chain inherits, the system SHALL add the node's id to the chain's `remove`.
enforced-by: tests/drafts/test_extending.py::test_removing_an_inherited_node_writes_remove
origin: src/kraft/drafts/extending.py §remove

## REQ the-editor-keeps-the-final-gate

IF the editor removes the final gate an extending chain inherits, THEN the system SHALL refuse the op.
enforced-by: tests/drafts/test_extending.py::test_the_editor_keeps_the_final_gate
origin: src/kraft/drafts/extending.py §remove

## REQ the-editor-leaves-nothing-naming-a-removed-node

IF an editor op on an extending chain would leave an `add` entry's `after`, a `reject_to` or a `restart_from` naming a node the chain removes, THEN the system SHALL refuse the op.
enforced-by: tests/drafts/test_extending.py::test_the_editor_leaves_nothing_naming_a_removed_node[remove-an-anchor], tests/drafts/test_extending.py::test_the_editor_leaves_nothing_naming_a_removed_node[remove-a-pointer-target], tests/drafts/test_extending.py::test_the_editor_leaves_nothing_naming_a_removed_node[restore-a-pointer-to-a-removed-node], tests/drafts/test_extending.py::test_the_editor_leaves_nothing_naming_a_removed_node[point-at-a-removed-node]
origin: src/kraft/drafts/extending.py §remove

## REQ restoring-a-removed-node-deletes-its-entry

WHEN the editor restores a removed node, the system SHALL delete its id from the chain's `remove`.
enforced-by: tests/drafts/test_extending.py::test_restoring_a_removed_node_deletes_its_entry
origin: src/kraft/drafts/extending.py §restore

## REQ an-editor-add-resolves-where-it-was-dropped

WHEN the editor adds a node to an extending chain at a position, the system SHALL write an `add` entry that resolves the node to that position.
enforced-by: tests/drafts/test_extending.py::test_an_add_resolves_where_it_was_dropped[after-an-inherited-node], tests/drafts/test_extending.py::test_an_add_resolves_where_it_was_dropped[before-an-earlier-add-on-that-node], tests/drafts/test_extending.py::test_an_add_resolves_where_it_was_dropped[after-an-added-node], tests/drafts/test_extending.py::test_an_add_resolves_where_it_was_dropped[at-the-end]
origin: src/kraft/drafts/extending.py §_place

## REQ the-editor-places-nothing-before-the-first-node

IF the editor adds or moves a node to the first position of an extending chain, THEN the system SHALL refuse the op.
enforced-by: tests/drafts/test_extending.py::test_nothing_goes_before_the_first_node[add_node], tests/drafts/test_extending.py::test_nothing_goes_before_the_first_node[move]
origin: src/kraft/drafts/extending.py §_place

## REQ moving-an-added-node-rewrites-its-entry

WHEN the editor moves a node an extending chain adds, the system SHALL rewrite that node's `add` entry so the node resolves where it was dropped.
enforced-by: tests/drafts/test_extending.py::test_moving_an_added_node_rewrites_its_entry[later], tests/drafts/test_extending.py::test_moving_an_added_node_rewrites_its_entry[earlier]
origin: src/kraft/drafts/extending.py §move

## REQ an-inherited-node-keeps-its-place

IF the editor moves a node an extending chain inherits, THEN the system SHALL refuse the op.
enforced-by: tests/drafts/test_extending.py::test_an_inherited_component_refuses_what_has_no_edit[move-node]
origin: src/kraft/drafts/extending.py §move

## REQ an-inherited-field-edit-is-an-override

WHEN the editor sets a field of a component an extending chain inherits, the system SHALL write it under the chain's `overrides` at the component's path.
enforced-by: tests/drafts/test_extending.py::test_an_inherited_field_edit_is_an_override[node-field], tests/drafts/test_extending.py::test_an_inherited_field_edit_is_an_override[task-model], tests/drafts/test_extending.py::test_an_inherited_field_edit_is_an_override[task-route-back-to-inherited], tests/drafts/test_extending.py::test_an_inherited_field_edit_is_an_override[dotted-policy-field], tests/drafts/test_extending.py::test_an_inherited_field_edit_is_an_override[back-to-the-inherited-value]
origin: src/kraft/drafts/extending.py §set_field

## REQ resetting-an-inherited-field-deletes-its-override

WHEN the editor resets a field of a component an extending chain inherits, the system SHALL delete that field's override, and every mapping that leaves empty.
enforced-by: tests/drafts/test_extending.py::test_resetting_an_inherited_field_deletes_its_override[one-field], tests/drafts/test_extending.py::test_resetting_an_inherited_field_deletes_its_override[every-field]
origin: src/kraft/drafts/extending.py §reset_field

## REQ an-editor-override-keeps-identity

IF the editor sets `id`, `kind`, `extends` or `chain_finalized` on a component an extending chain inherits, THEN the system SHALL refuse the op.
enforced-by: tests/drafts/test_extending.py::test_an_editor_override_keeps_identity[id], tests/drafts/test_extending.py::test_an_editor_override_keeps_identity[kind], tests/drafts/test_extending.py::test_an_editor_override_keeps_identity[extends], tests/drafts/test_extending.py::test_an_editor_override_keeps_identity[chain_finalized]
origin: src/kraft/drafts/extending.py §set_field

## REQ a-structural-op-on-an-inherited-component-is-refused

IF an editor op other than a node's remove, a field's set or reset, or a removed node's restore addresses a component an extending chain inherits, THEN the system SHALL refuse it with 422.
enforced-by: tests/drafts/test_extending.py::test_an_inherited_component_refuses_what_has_no_edit[move-node], tests/drafts/test_extending.py::test_an_inherited_component_refuses_what_has_no_edit[rename-node], tests/drafts/test_extending.py::test_an_inherited_component_refuses_what_has_no_edit[add_step], tests/drafts/test_extending.py::test_an_inherited_component_refuses_what_has_no_edit[add_task], tests/drafts/test_extending.py::test_an_inherited_component_refuses_what_has_no_edit[remove-task], tests/drafts/test_extending.py::test_an_inherited_component_refuses_what_has_no_edit[set_fragment], tests/drafts/test_extending.py::test_an_inherited_component_refuses_what_has_no_edit[extend], tests/drafts/test_extending.py::test_an_inherited_component_refuses_what_has_no_edit[move_to_library]
origin: src/kraft/drafts/extending.py §inside

## REQ an-op-inside-an-added-node-edits-its-entry

WHEN an editor op addresses a component inside a node an extending chain adds, the system SHALL apply it to that node inside its `add` entry.
enforced-by: tests/drafts/test_extending.py::test_an_op_inside_an_added_node_edits_its_entry[add_task], tests/drafts/test_extending.py::test_an_op_inside_an_added_node_edits_its_entry[set_field], tests/drafts/test_extending.py::test_an_op_inside_an_added_node_edits_its_entry[remove-task], tests/drafts/test_extending.py::test_an_op_inside_an_added_node_edits_its_entry[move_to_library]
origin: src/kraft/drafts/extending.py §inside

## REQ renaming-an-added-node-renames-its-pointers

WHEN the editor renames a node an extending chain adds, the system SHALL rename every `reject_to` and `restart_from` naming it, in added nodes and as overrides of inherited ones.
enforced-by: tests/drafts/test_extending.py::test_renaming_an_added_node_renames_its_pointers[added-gate], tests/drafts/test_extending.py::test_renaming_an_added_node_renames_its_pointers[inherited-gate-override]
origin: src/kraft/drafts/extending.py §rename

## REQ a-new-chain-can-extend-a-chain

WHEN `new_chain` names `extends` (the Extend choice of the editor's Duplicate or Add to my library), the system SHALL write a chain holding only its id and that `extends`.
enforced-by: tests/drafts/test_extending.py::test_a_new_chain_can_extend_a_chain, tests/api/test_addon_content.py::test_a_new_chain_can_extend_an_addon_chain
origin: src/kraft/drafts/ops.py §new_chain

## REQ a-new-chain-extends-only-a-chain-it-may

IF `new_chain` names `extends` together with `from`, or names a chain the library does not hold, THEN the system SHALL refuse it.
enforced-by: tests/drafts/test_extending.py::test_a_new_chain_cannot_extend_what_it_may_not[unknown], tests/drafts/test_extending.py::test_a_new_chain_cannot_extend_what_it_may_not[with-from]
origin: src/kraft/drafts/ops.py §new_chain

## REQ detach-writes-the-resolved-chain

WHEN the editor detaches an extending chain, the system SHALL write the chain's resolved nodes as its own `nodes` and SHALL drop its `extends`, `remove`, `add` and `overrides`.
enforced-by: tests/drafts/test_extending.py::test_detach_writes_the_resolved_chain
origin: src/kraft/drafts/extending.py §detach

## REQ detach-keeps-untouched-nodes-as-authored

WHEN the editor detaches an extending chain, the system SHALL write each node no override names as the file that defines it wrote it.
enforced-by: tests/drafts/test_extending.py::test_detach_keeps_untouched_nodes_as_authored[inherited], tests/drafts/test_extending.py::test_detach_keeps_untouched_nodes_as_authored[added], tests/drafts/test_extending.py::test_detach_keeps_untouched_nodes_as_authored[two-levels]
origin: src/kraft/drafts/extending.py §detach

## REQ an-extending-chains-sources-name-the-parent

WHEN a draft of an extending chain resolves, the system SHALL give a field its parent chain sets the source `chain:<parent id>`, and a field its own override sets the source `chain`.
enforced-by: tests/drafts/test_resolve.py::test_an_extending_chains_sources_name_the_parent[inherited], tests/drafts/test_resolve.py::test_an_extending_chains_sources_name_the_parent[overridden], tests/drafts/test_resolve.py::test_an_extending_chains_sources_name_the_parent[added], tests/drafts/test_resolve.py::test_an_extending_chains_sources_name_the_parent[two-levels]
origin: src/kraft/drafts/resolve.py §_layers

## REQ the-draft-places-removed-parent-nodes

WHEN a draft of an extending chain resolves, the system SHALL list each node it removes from its parent with the nearest earlier node it still runs.
enforced-by: tests/drafts/test_resolve.py::test_the_draft_places_removed_parent_nodes
origin: src/kraft/drafts/resolve.py §_extending

## REQ an-unfinished-extending-draft-still-lists-its-nodes

WHEN a draft of an extending chain applies its edits but does not validate, the system SHALL still list the chain's nodes in resolved order, each with where it came from.
enforced-by: tests/drafts/test_resolve.py::test_an_unfinished_extending_draft_still_lists_its_nodes
origin: src/kraft/drafts/resolve.py §_extending

## REQ a-problem-in-an-edit-lands-on-its-component

WHEN a problem or icon issue of an extending chain sits in an edit, the system SHALL report it at the canonical path the edit addresses.
enforced-by: tests/drafts/test_resolve.py::test_a_problem_in_an_edit_lands_on_its_component[override], tests/drafts/test_resolve.py::test_a_problem_in_an_edit_lands_on_its_component[added-node], tests/drafts/test_resolve.py::test_a_problem_in_an_edit_lands_on_its_component[remove]
origin: src/kraft/templates/library.py §_Resolution.split

## REQ a-parent-change-is-a-draft-change

WHEN a draft adds, changes or drops a chain's `extends`, the system SHALL list it among the draft's changes.
enforced-by: tests/drafts/test_resolve.py::test_a_parent_change_is_a_draft_change[detached], tests/drafts/test_resolve.py::test_a_parent_change_is_a_draft_change[changed]
origin: src/kraft/drafts/resolve.py §_extends_change

## REQ the-canvas-marks-where-a-node-comes-from

WHEN the canvas draws an extending chain, the system SHALL mark each node added or overridden there, and SHALL draw each removed parent node as a ghost after the node it followed.
enforced-by: frontend/src/ng/templates/ChainCanvas.test.tsx::an extending chain > marks added and overridden nodes and draws removed ones as ghosts
origin: frontend/src/ng/templates/ChainCanvas.tsx

## REQ no-editor-op-leaves-the-edits-not-applying

IF an editor op would leave an extending chain whose edits applied with edits that no longer apply to its parent, THEN the system SHALL refuse the op.
enforced-by: tests/drafts/test_extending.py::test_no_op_leaves_the_edits_not_applying[a-second-final-gate], tests/drafts/test_extending.py::test_no_op_leaves_the_edits_not_applying[a-looser-policy]
origin: src/kraft/drafts/ops.py §apply

## REQ renaming-a-library-component-reaches-an-extending-chains-edits

WHEN the editor renames a library component, the system SHALL rename each use of it inside an extending chain's added nodes and override values.
enforced-by: tests/drafts/test_ops.py::test_renaming_a_library_component_rewrites_an_extending_chains_uses[added-node], tests/drafts/test_ops.py::test_renaming_a_library_component_rewrites_an_extending_chains_uses[added-task], tests/drafts/test_ops.py::test_renaming_a_library_component_rewrites_an_extending_chains_uses[override-list], tests/drafts/test_ops.py::test_renaming_a_library_component_rewrites_an_extending_chains_uses[override-steering]
origin: src/kraft/drafts/ops.py §_components

## REQ an-extending-chain-sets-only-its-description-and-policy

IF an editor op on an extending chain writes the chain itself other than its `description` or `policy`, THEN the system SHALL refuse the op.
enforced-by: tests/drafts/test_extending.py::test_an_inherited_component_refuses_what_has_no_edit[unset-extends], tests/drafts/test_extending.py::test_an_inherited_component_refuses_what_has_no_edit[write-add], tests/drafts/test_extending.py::test_an_inherited_component_refuses_what_has_no_edit[reset-the-chain], tests/drafts/test_extending.py::test_an_inherited_component_refuses_what_has_no_edit[set_fragment-the-chain]
origin: src/kraft/drafts/extending.py §_chain_field

## REQ removing-an-overridden-node-keeps-its-overrides

IF the editor removes a node an extending chain inherits while the chain overrides it, THEN the system SHALL refuse the op rather than drop the overrides.
enforced-by: tests/drafts/test_extending.py::test_an_inherited_component_refuses_what_has_no_edit[remove-an-overridden-node]
origin: src/kraft/drafts/extending.py §remove

## REQ changing-an-extending-chains-base-keeps-what-fits

WHEN the editor changes the chain an extending chain extends, the system SHALL keep each `remove`, `add` and `overrides` entry the new parent can take and SHALL drop each one it cannot, saying which and why. A removal the new parent cannot take includes one of a node that a node it keeps still names in `reject_to` or `restart_from`, after this chain's overrides.
enforced-by: tests/drafts/test_extending.py::test_a_chain_base_check_says_which_edits_the_new_parent_takes, tests/drafts/test_extending.py::test_changing_a_chains_base_drops_the_edits_it_cant_take[as-filed], tests/drafts/test_extending.py::test_changing_a_chains_base_drops_the_edits_it_cant_take[with-an-override-it-cant-take], tests/drafts/test_extending.py::test_a_new_parents_pointer_keeps_the_node_it_names[reject_to], tests/drafts/test_extending.py::test_a_new_parents_pointer_keeps_the_node_it_names[restart_from], tests/drafts/test_extending.py::test_a_new_parents_pointer_keeps_the_node_it_names[reject_to-elsewhere], tests/drafts/test_extending.py::test_a_new_parents_pointer_keeps_the_node_it_names[restart_from-elsewhere]
origin: src/kraft/drafts/extending.py §change_chain_base

## REQ a-chains-new-base-is-one-it-may-extend

IF the editor changes an extending chain's base to itself, to a chain that extends it, to no chain the library holds, or to a repo library's chain, THEN the system SHALL refuse the op.
enforced-by: tests/drafts/test_extending.py::test_a_chains_base_is_one_it_may_extend[itself], tests/drafts/test_extending.py::test_a_chains_base_is_one_it_may_extend[cycle], tests/drafts/test_extending.py::test_a_chains_base_is_one_it_may_extend[unknown], tests/api/test_addon_content.py::test_a_repo_librarys_content_is_edited_in_its_repository[repo-library-base]
origin: src/kraft/drafts/extending.py §_new_parent

## REQ an-inherited-entry-is-marked

WHERE a chain or library component is served under the namespace of an add-on that has a parent, the system SHALL report it as inherited (naming the parent and the add-on that wrote it), overridden or added, and SHALL mark each node of such a chain whose components the add-on overrides or adds.
enforced-by: tests/templates/test_library_inheritance.py::test_inheritance_marks[inherited-chain], tests/templates/test_library_inheritance.py::test_inheritance_marks[inherited-through-two], tests/templates/test_library_inheritance.py::test_inheritance_marks[overridden-component], tests/templates/test_library_inheritance.py::test_inheritance_marks[overridden-chain], tests/templates/test_library_inheritance.py::test_inheritance_marks[added-component], tests/templates/test_library_inheritance.py::test_inheritance_marks[added-chain], tests/templates/test_library_inheritance.py::test_inheritance_marks[same-name-other-section], tests/templates/test_library_inheritance.py::test_inheritance_marks[steering], tests/templates/test_library_inheritance.py::test_inheritance_marks[alias-parent], tests/templates/test_library_inheritance.py::test_inheritance_marks[root-addon], tests/templates/test_library_inheritance.py::test_inheritance_marks[local], tests/templates/test_library_inheritance.py::test_inheritance_marks[layer], tests/templates/test_catalogue.py::test_node_inheritance[inherited-chain-overridden-task], tests/templates/test_catalogue.py::test_node_inheritance[extending-chain], tests/templates/test_catalogue.py::test_node_inheritance[overridden-beats-added], tests/templates/test_catalogue.py::test_node_inheritance[root-chain], tests/templates/test_catalogue.py::test_node_inheritance[local-chain], tests/api/test_addon_content.py::test_a_child_addons_entries_carry_their_marks[chains-list], tests/api/test_addon_content.py::test_a_child_addons_entries_carry_their_marks[library], tests/api/test_addon_content.py::test_a_child_addons_entries_carry_their_marks[resolved], tests/api/test_addon_content.py::test_a_child_addons_entries_carry_their_marks[draft-view], tests/api/test_addon_content.py::test_a_child_addons_entries_carry_their_marks[no-layer-names]
origin: src/kraft/templates/library.py §inheritance
