"""`collection.json` and `plugin.json`: what a manifest may say, the fixed
layout beside it, and which Kraft a plugin runs on."""

import json
from pathlib import Path

import jsonschema
import pytest

from kraft.plugins import manifest

SCHEMAS = Path(__file__).resolve().parents[2] / "docsite" / "public" / "schemas"
OWNER = {"name": "ACME Platform", "email": "platform@acme.dev"}
COLLECTION = {
    "name": "acme",
    "owner": OWNER,
    "plugins": [{"name": "release", "source": "./plugins/release"}],
}
PLUGIN = {"name": "release", "version": "1.4.0", "requires": {"kraft": "2.4"}}
FILES = {"library.yaml": ("100644", b"tasks: {}\n")}


def _entries(*sources):
    return [{"name": f"p{n}", "source": s} for n, s in enumerate(sources)]


@pytest.mark.parametrize(
    "change",
    [
        {"name": "Acme"},
        {"name": "a" * 65},
        {"owner": None},
        {"owner": {"name": "ACME"}},
        {"plugins": _entries("./plugins/../x")},
        {"plugins": _entries("plugins/release")},
        {"plugins": [{"name": "release", "source": {"source": "github", "repo": "a/b"}}]},
        {"plugins": _entries("./")},
        {"plugins": _entries("./.kraft/x")},
        {"plugins": _entries("./.KRAFT/x")},
        {"plugins": [COLLECTION["plugins"][0], {"name": "release", "source": "./other"}]},
        {"plugins": _entries("./plugins", "./plugins/release")},
    ],
    ids=[
        "bad-name",
        "name-too-long",
        "missing-owner",
        "owner-without-email",
        "source-with-dotdot",
        "source-without-dot-slash",
        "other-repo-source",
        "root-source",
        "source-inside-kraft-dir",
        "source-inside-KRAFT-dir",
        "duplicate-entry",
        "nested-source",
    ],
)
def test_a_collection_is_refused(change):
    data = {k: v for k, v in {**COLLECTION, **change}.items() if v is not None}
    with pytest.raises(manifest.ManifestError, match="collection.json"):
        manifest.collection(data, "collection.json")


def _plugin(change=None, *, entry="release", files=FILES, text=None):
    data = {k: v for k, v in {**PLUGIN, **(change or {})}.items() if v is not None}
    parsed = manifest.parse(text if text is not None else json.dumps(data), "plugin.json")
    found = manifest.plugin(parsed, "plugin.json")
    manifest.check_plugin(found, entry, files)
    return found


@pytest.mark.parametrize(
    "kwargs",
    [
        {"change": {"name": "kraft"}, "entry": "kraft"},
        {"change": {"name": "Release"}},
        {"entry": "shipping"},
        {"files": {**FILES, "profiles.yaml": ("100644", b"harnesses: {x: {provider: codex}}\n")}},
        {"change": {"requires": None}},
        {"change": {"requires": {}}},
        {"change": {"requires": {"kraft": "2.4", "python": "3.12"}}},
        {"change": {"version": None}},
        {"change": {"version": "1.4"}},
        {"change": {"author": {"name": "Someone"}}},
        {"files": {".kraft/plugin.json": ("100644", b"{}")}},
        {"text": json.dumps({**PLUGIN, "description": "a‮b"})},
        {"text": json.dumps({**PLUGIN, "version": "1.4." + "9" * 80})},
    ],
    ids=[
        "kraft-name",
        "bad-identifier",
        "name-differs-from-entry",
        "harnesses-section",
        "missing-requires",
        "missing-requires-kraft",
        "unknown-requires-key",
        "missing-version",
        "non-semver-version",
        "author-without-email",
        "no-components",
        "hidden-character-in-a-string",
        "a-version-too-long-to-compare",
    ],
)
def test_a_plugin_is_refused(kwargs):
    with pytest.raises(manifest.ManifestError):
        _plugin(**kwargs)


def _verdict(requires: str, running: str) -> str | None:
    try:
        found = _plugin({"requires": {"kraft": requires}})
    except manifest.ManifestError:
        return "refused"
    if manifest.is_source_build(running):
        return "skipped"
    return manifest.kraft_compatible(found.requires.kraft, running)


@pytest.mark.parametrize(
    ("requires", "running", "expected"),
    [
        ("2.4", "2.6.1", None),
        ("2.4", "2.4.0", None),
        ("2.4", "2.3.9", "needs Kraft 2.4 or later"),
        ("3.0", "2.9.0", "needs Kraft 3.x"),
        ("1.2", "2.4.0", "needs Kraft 1.x"),
        ("2", "2.0.0", None),
        ("2.4.1", "2.4.1", "refused"),
        ("2.4", "2.4.0rc1", None),
        ("2.4", "0.1.dev50+gea085c4ae", "skipped"),
        (">=2.4", "2.4.0", "refused"),
    ],
    ids=[
        "same-major-above-minimum",
        "exactly-minimum",
        "below-minimum",
        "next-major",
        "earlier-major",
        "major-only",
        "patch-refused",
        "release-candidate",
        "source-build-skipped",
        "malformed",
    ],
)
def test_kraft_compatibility(requires, running, expected):
    assert _verdict(requires, running) == expected


CLAUDE_CODE = {"metadata": {"version": "1"}, "category": "x", "tags": ["a"], "strict": True}


@pytest.mark.parametrize(
    ("build", "data", "model", "unknown"),
    [
        (
            manifest.collection,
            {**COLLECTION, "homepage": "x"},
            manifest.CollectionManifest,
            ["homepage"],
        ),
        (manifest.plugin, {**PLUGIN, "keywords": ["a"]}, manifest.PluginManifest, ["keywords"]),
        (manifest.plugin, {**PLUGIN, **CLAUDE_CODE}, manifest.PluginManifest, sorted(CLAUDE_CODE)),
    ],
    ids=["collection", "plugin", "claude-code-keys"],
)
def test_unknown_keys_are_ignored(build, data, model, unknown):
    """A manifest written for Claude Code, or for a newer Kraft, still loads;
    the keys nobody reads are listed so `validate` can warn about them."""
    assert build({**data, "$schema": "https://example.com/s.json"}, "m.json").name
    assert manifest.unknown_keys(data, model) == unknown


def test_components_are_read_from_the_fixed_layout():
    layout = {
        "library.yaml": True,
        "profiles.yaml": True,
        ".kraft/plugin.json": True,
        "chains/ship.yaml": True,
        "skills/deploy-review/SKILL.md": True,
        "chains/Ship.yaml": False,
        "chains/sub/a.yaml": False,
        "library.yml": False,
        "skills/x/scripts/run.sh": False,
        "scripts/x.sh": False,
        ".mcp.json": False,
    }
    assert {rel: manifest.in_layout(rel) for rel in layout} == layout
    assert manifest.near_layout("chains/Ship.yaml") and manifest.near_layout("library.yml")
    assert not manifest.near_layout("scripts/x.sh")
    assert manifest.shadows_layout("skills") and manifest.shadows_layout("skills/x")
    assert not manifest.shadows_layout("skills/x/scripts")


#: Manifests both the published schema and the loader can judge. What only
#: Kraft can check (unique entries, nested sources, the reserved name, a bad
#: email: `format` is an annotation to a JSON Schema validator) is not here.
_CORPUS = [
    ("collection", COLLECTION, True),
    ("collection", {**COLLECTION, "name": "Acme"}, False),
    ("collection", {**COLLECTION, "plugins": []}, False),
    ("collection", {**COLLECTION, "plugins": _entries("plugins/release")}, False),
    ("collection", {**COLLECTION, "plugins": _entries("./plugins/../x")}, False),
    ("collection", {**COLLECTION, "plugins": _entries("./.KRAFT/x")}, False),
    ("collection", {**COLLECTION, "metadata": {"x": 1}}, True),
    ("plugin", PLUGIN, True),
    ("plugin", {**PLUGIN, "version": "1.4"}, False),
    ("plugin", {**PLUGIN, "version": "2.0.0-rc.1"}, True),
    ("plugin", {**PLUGIN, "version": "1.4." + "9" * 80}, False),
    ("plugin", {**PLUGIN, "requires": {"kraft": "2.4.1"}}, False),
    ("plugin", {**PLUGIN, "requires": {"kraft": "2", "python": "3"}}, False),
    ("plugin", {**PLUGIN, "requires": {"kraft": "2", "harnesses": ["claude"]}}, True),
    ("plugin", {**PLUGIN, "homepage": "http://acme.dev"}, False),
    ("plugin", {**PLUGIN, **CLAUDE_CODE}, True),
]


@pytest.mark.parametrize(
    ("kind", "data", "valid"), _CORPUS, ids=[str(n) for n in range(len(_CORPUS))]
)
def test_the_schemas_agree_with_the_loader(kind, data, valid):
    """The schemas are written by hand, so one corpus goes through both."""
    schema = json.loads((SCHEMAS / f"{kind}.schema.json").read_text())
    by_schema = jsonschema.Draft202012Validator(schema).is_valid(data)
    try:
        getattr(manifest, kind)(data, f"{kind}.json")
        by_loader = True
    except manifest.ManifestError:
        by_loader = False
    assert (by_schema, by_loader) == (valid, valid)
