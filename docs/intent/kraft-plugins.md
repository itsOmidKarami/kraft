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
