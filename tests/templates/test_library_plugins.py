"""The template library with plugin layers: a plugin's declarations are
addressed as `<namespace>:<name>`, and a bare local name stays local."""

import pytest
from support.plugins import AGENT, chain, home, installed

from kraft import harness
from kraft.templates.environment import HarnessProfileTable, TemplateEnvironmentError
from kraft.templates.library import TemplateLibrary, TemplateLibraryError


def _chain_id(tmp_path):
    TemplateLibrary.from_yaml_dir(
        home(tmp_path, chains={"ship": {**chain("base"), "id": "release:ship"}})
    )


def _library_key(tmp_path):
    TemplateLibrary.from_yaml_dir(home(tmp_path, library={"tasks": {"release:base": AGENT}}))


def _profile_id(tmp_path):
    HarnessProfileTable.from_mapping(
        {"profiles": {"release:deep": {"model": {"codex": "m"}}}},
        tmp_path / "harnesses.yaml",
        harnesses=harness.load(None).valid,
    )


@pytest.mark.parametrize(
    "load", [_chain_id, _library_key, _profile_id], ids=["chain-id", "library-key", "profile-id"]
)
def test_a_local_declaration_with_a_colon_is_refused(tmp_path, load):
    with pytest.raises(
        (TemplateLibraryError, TemplateEnvironmentError), match="declared bare|must match"
    ):
        load(tmp_path)


#: A plugin named `release`. `implementer` reaches `base` and `house` by bare
#: name; `own` reaches `base` by the plugin's own qualified name.
PLUGIN_LIBRARY = {
    "steering": {"house": {"instructions": "release rules"}},
    "tasks": {
        "base": {"kind": "agent", "harness": "codex", "prompt": "plugin base"},
        "implementer": {"extends": "base", "steering": ["house"]},
        "own": {"extends": "release:base", "prompt": "own qualified"},
        "tiered": {"extends": "base", "profile": "strong"},
        "methodical": {"extends": "base", "skill": "deploy-review"},
    },
}
PLUGIN_CHAINS = {
    "ship": chain("implementer"),
    "own": chain("own"),
    "tiered": chain("tiered"),
    "skilled": chain("methodical"),
}


def _load(tmp_path, *, alias=None, local_extends="base"):
    plugin = installed(
        tmp_path,
        "release",
        alias=alias,
        library=PLUGIN_LIBRARY,
        chains=PLUGIN_CHAINS,
        skills={"deploy-review": "plugin method"},
    )
    root = home(tmp_path, chains={"local": chain(local_extends)})
    return TemplateLibrary.from_yaml_dir(root, plugins=[plugin])


def _task(library, id):
    return next(iter(library.resolve_chain(id).nodes[0].tasks())).task


@pytest.mark.parametrize(
    ("alias", "local_extends", "chain_id", "field", "expected"),
    [
        (None, "release:implementer", "local", "prompt", "plugin base"),
        (None, "implementer", "local", None, "extends no task named 'implementer'"),
        (None, "base", "release:ship", "prompt", "plugin base"),
        (None, "base", "release:ship", "steering", ["release:house"]),
        (None, "base", "release:skilled", "skill", "release:deploy-review"),
        (None, "base", "release:tiered", "profile", "strong"),
        ("rel", "rel:implementer", "local", "steering", ["rel:house"]),
        ("rel", "base", "rel:own", "prompt", "own qualified"),
    ],
    ids=[
        "local-extends-plugin",
        "bare-local-is-not-a-plugin-component",
        "plugin-bare-extends",
        "plugin-bare-steering",
        "plugin-bare-skill",
        "plugin-bare-profile-is-instance",
        "alias-from-outside",
        "alias-own-qualified-ref",
    ],
)
def test_plugin_layer_resolution(tmp_path, alias, local_extends, chain_id, field, expected):
    """`field` of the one task chain `chain_id` resolves to; `field` None means
    the chain must not resolve, with `expected` in the error."""
    library = _load(tmp_path, alias=alias, local_extends=local_extends)
    if field is None:
        with pytest.raises(TemplateLibraryError, match=expected):
            library.resolve_chain(chain_id)
        return
    assert getattr(_task(library, chain_id), field) == expected


def test_with_library_keeps_plugin_layers(tmp_path):
    """An edit of the local `library.yaml` is checked against a candidate; the
    candidate must still hold the plugin, or every local `extends: release:x`
    would break on save."""
    library = _load(tmp_path, local_extends="release:implementer")
    candidate = library.with_library({"tasks": {"base": AGENT}}, tmp_path / "library.yaml")
    assert "release:ship" in candidate.chain_ids
    assert _task(candidate, "local").prompt == "plugin base"


def test_a_plugin_without_a_library_file_loads(tmp_path):
    plugin = installed(
        tmp_path,
        "release",
        chains={"plain": {"nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t", **AGENT}]}]}},
    )
    library = TemplateLibrary.from_yaml_dir(home(tmp_path), plugins=[plugin])
    assert "release:plain" in library.chain_ids
    assert _task(library, "release:plain").prompt == "local base"


_BOMB = "a: &a [x,x,x,x,x,x,x,x,x,x]\n" + "".join(
    f"{k}: &{k} [{','.join(['*' + p] * 10)}]\n" for p, k in zip("abcd", "bcde", strict=True)
)


@pytest.mark.parametrize(
    ("file", "text", "why"),
    [
        ("library.yaml", "tasks: {'x:y': {kind: agent}}", "declared bare"),
        ("library.yaml", _BOMB, "more YAML nodes"),
        ("chains/bad.yaml", "id: 'other:ship'\nnodes: []\n", "declared bare"),
        ("chains/bad.yaml", "- not\n- a mapping\n", "expected a mapping"),
    ],
    ids=["qualified-key", "yaml-bomb", "qualified-chain-id", "not-a-mapping"],
)
def test_a_plugin_error_names_the_store_file(tmp_path, file, text, why):
    plugin = installed(tmp_path, "release")
    (plugin.root / file).parent.mkdir(exist_ok=True)
    (plugin.root / file).write_text(text)
    with pytest.raises(TemplateLibraryError, match=why) as exc:
        TemplateLibrary.from_yaml_dir(home(tmp_path), plugins=[plugin])
    assert str(plugin.root / file) in str(exc.value)


def test_lint_dir_skips_plugin_layers(tmp_path):
    """Offline (`plugins=None`) a chain that reaches into a plugin namespace is
    reported as not checked, not as broken. With the instance's plugins given,
    even none, the same reference is an error."""
    root = home(tmp_path, chains={"local": chain("release:implementer"), "plain": chain("base")})

    offline = TemplateLibrary.lint_dir(root, plugins=None)
    assert (offline.chains, offline.issues) == (("plain",), ())
    assert [(i.chain, i.message.endswith("not checked (plugin)")) for i in offline.unchecked] == [
        ("local", True)
    ]

    online = TemplateLibrary.lint_dir(root, plugins=())
    assert [i.chain for i in online.issues] == ["local"]
    assert online.unchecked == ()
