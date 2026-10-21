"""Kraft Lite against Kraft: the plugin's bundled copy of the packaged library is
the repo's own, and its dependency-free YAML reader agrees with `yaml.safe_load`
on every config file Kraft ships. The plugin's own tests cannot check either: the
directory is published as its own repo, with no `config/` above it and no PyYAML."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins" / "kraft-lite"
CONFIG = REPO / "config"


@pytest.fixture(scope="module")
def kl_modules():
    """The plugin's modules, importable for the module's tests. Today that is
    just `kl_yaml`, returned in a tuple that grows as the plugin's modules do."""
    sys.path.insert(0, str(PLUGIN))
    try:
        import kl_yaml

        yield (kl_yaml,)
    finally:
        sys.path.remove(str(PLUGIN))
        sys.modules.pop("kl_yaml", None)


def _plugin_corpus() -> dict[str, tuple[str, object]]:
    """`CORPUS` from the plugin's own YAML tests. Both test trees are packages
    named `tests`, so it is loaded by path under a name of its own."""
    sys.path.insert(0, str(PLUGIN))
    try:
        path = PLUGIN / "tests" / "test_yaml.py"
        spec = importlib.util.spec_from_file_location("kraft_lite_plugin_test_yaml", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.CORPUS
    finally:
        sys.path.remove(str(PLUGIN))
        sys.modules.pop("kl_yaml", None)


CORPUS = _plugin_corpus()
CONFIG_FILES = sorted(CONFIG.rglob("*.yaml"))
# What Kraft Lite reads: the library and its chains. Their copy must load
# with the subset reader; any other config file may be refused, never misread.
LIBRARY_FILES = {
    p
    for p in CONFIG_FILES
    if p.relative_to(CONFIG).as_posix() == "library.yaml" or p.parent == CONFIG / "chains"
}


def test_the_bundled_library_is_the_packaged_one():
    expected = {"library.yaml"} | {"chains/" + p.name for p in (CONFIG / "chains").glob("*.yaml")}
    bundled = {
        p.relative_to(PLUGIN / "config").as_posix()
        for p in (PLUGIN / "config").rglob("*")
        if p.is_file()
    }

    assert bundled == expected, "run: just kraft-lite-config"
    for rel in sorted(expected):
        assert (PLUGIN / "config" / rel).read_bytes() == (CONFIG / rel).read_bytes(), (
            f"{rel} differs; run: just kraft-lite-config"
        )


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_the_subset_reader_matches_safe_load_on_the_corpus(kl_modules, name):
    (kl_yaml,) = kl_modules
    text, _ = CORPUS[name]

    assert kl_yaml.load(text) == yaml.safe_load(text)


@pytest.mark.parametrize(
    "path",
    [pytest.param(p, id=p.relative_to(REPO).as_posix()) for p in CONFIG_FILES],
)
def test_the_subset_reader_matches_safe_load_on_kraft_config(kl_modules, path):
    (kl_yaml,) = kl_modules
    text = path.read_text()
    expected = yaml.safe_load(text)

    if path in LIBRARY_FILES:
        assert kl_yaml.load(text, str(path)) == expected
        return
    try:
        got = kl_yaml.load(text, str(path))
    except kl_yaml.YamlError:
        return  # refused out loud is fine; a different value is not
    assert got == expected


@pytest.mark.parametrize("rel", sorted(p.relative_to(CONFIG).as_posix() for p in LIBRARY_FILES))
def test_the_bundled_copy_loads_with_the_subset_reader(kl_modules, rel):
    (kl_yaml,) = kl_modules
    text = (PLUGIN / "config" / rel).read_text()

    assert kl_yaml.load(text, rel) == yaml.safe_load(text)
