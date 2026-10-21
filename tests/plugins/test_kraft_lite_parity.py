"""Kraft Lite against Kraft: the plugin's bundled copy of the packaged library is
the repo's own, and its dependency-free YAML reader agrees with `yaml.safe_load`
on every config file Kraft ships. The plugin's own tests cannot check either: the
directory is published as its own repo, with no `config/` above it and no PyYAML."""

from __future__ import annotations

import importlib.util
import shlex
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from kraft.templates.library import TemplateLibrary

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins" / "kraft-lite"
CONFIG = REPO / "config"


@pytest.fixture(scope="module")
def kl_modules():
    """The plugin's modules, importable for the module's tests: `kl_yaml` and
    `kl_chains`, in a tuple that grows as the plugin's modules do."""
    sys.path.insert(0, str(PLUGIN))
    try:
        import kl_chains
        import kl_yaml

        yield (kl_yaml, kl_chains)
    finally:
        sys.path.remove(str(PLUGIN))
        sys.modules.pop("kl_yaml", None)
        sys.modules.pop("kl_chains", None)


def _plugin_yaml_tests():
    """The plugin's own YAML tests, for their `CORPUS` and `SAFE_LOAD_REFUSES`.
    Both test trees are packages named `tests`, so it is loaded by path under a
    name of its own."""
    sys.path.insert(0, str(PLUGIN))
    try:
        path = PLUGIN / "tests" / "test_yaml.py"
        spec = importlib.util.spec_from_file_location("kraft_lite_plugin_test_yaml", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(PLUGIN))
        sys.modules.pop("kl_yaml", None)


_PLUGIN_YAML_TESTS = _plugin_yaml_tests()
CORPUS = _PLUGIN_YAML_TESTS.CORPUS
SAFE_LOAD_REFUSES = _PLUGIN_YAML_TESTS.SAFE_LOAD_REFUSES
# Tracked files only: a local, gitignored one (`access.yaml`, `notify.yaml`)
# holds secrets a mismatch would print, and is not Kraft's to ship.
CONFIG_FILES = sorted(
    REPO / rel
    for rel in subprocess.run(
        ["git", "ls-files", "-z", "--", "config"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    if rel.endswith(".yaml")
)
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
    kl_yaml, _ = kl_modules
    text, _ = CORPUS[name]

    assert kl_yaml.load(text) == yaml.safe_load(text)


@pytest.mark.parametrize("name", sorted(SAFE_LOAD_REFUSES))
def test_the_subset_reader_refuses_where_safe_load_raises(kl_modules, name):
    kl_yaml, _ = kl_modules
    text, _, _ = SAFE_LOAD_REFUSES[name]

    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(text)
    with pytest.raises(kl_yaml.YamlError):
        kl_yaml.load(text)


@pytest.mark.parametrize(
    "path",
    [pytest.param(p, id=p.relative_to(REPO).as_posix()) for p in CONFIG_FILES],
)
def test_the_subset_reader_matches_safe_load_on_kraft_config(kl_modules, path):
    kl_yaml, _ = kl_modules
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
    kl_yaml, _ = kl_modules
    text = (PLUGIN / "config" / rel).read_text()

    assert kl_yaml.load(text, rel) == yaml.safe_load(text)


#: Chains that extend chains, two levels deep, over the packaged library.
EXTENDING = {
    "ship": """\
        extends: default
        remove: [external_approval]
        overrides:
          implementation.main.implement: {prompt: Build it.}
          verification.fix_loop.judge: {prompt: Be strict.}
          merge_request_feedback.on_failure.repair: {tasks: [{id: fix, extends: repair_mr_checks}]}
        add:
          - after: work_brief
            node: {id: lint, kind: exec, tasks: [{id: run, extends: verify_changed_scopes}]}
          - after: local_review
            node:
              id: typecheck
              kind: exec
              policy: {max_attempts: 5}
              tasks:
                - {id: tsc, kind: subprocess, command: "sh -c 'npm run lint && npx tsc --noEmit'"}
              fix_loop: {tasks: [{id: fix, extends: repair_mr_checks}]}
        """,
    "ship-fast": """\
        extends: ship
        remove: [spec, spec_approval]
        overrides:
          verification: {fix_loop: {max_attempts: 4}}
          mark_ready: {steps: [{id: ready, tasks: [{id: ready, extends: mark_mr_ready}]}]}
          lint.main.run: {scope: once}
        """,
}


def _project_kraft(resolved) -> list:
    """`TemplateLibrary.resolve_chain`'s answer, reduced to what Lite reads."""

    def task(t):
        command = getattr(t.task, "command", None)
        return (
            t.path,
            t.task.kind.value,
            *(getattr(t.task, key, None) for key in ("skill", "prompt", "produces")),
            *(getattr(getattr(t.task, key, None), "value", None) for key in ("ref", "target")),
            # What Kraft runs (`executor.dispatch`): the string split like a shell.
            shlex.split(command) if command is not None else None,
        )

    def steps(group):
        return [(s.id, [task(t) for t in s.tasks]) for s in group]

    out = []
    for n in resolved.nodes:
        if n.node.kind.value == "gate":
            node = n.node
            out.append((n.id, "gate", node.artifact, node.reject_to, node.chain_finalized))
            continue
        loop = n.node.fix_loop
        base = n.node.on_base_changed
        policy = n.node.policy
        out.append(
            (
                n.id,
                "exec",
                policy.max_attempts if policy else None,
                steps(n.steps),
                steps(n.on_failure),
                (steps(n.fix_loop), task(n.judge) if n.judge else None, loop.max_attempts)
                if loop
                else None,
                base.restart_from if base else None,
            )
        )
    return out


def _project_lite(chain: dict) -> list:
    """`kl_chains.resolve`'s answer, reduced the same way."""

    def task(t):
        return tuple(
            t[key]
            for key in ("path", "kind", "skill", "prompt", "produces", "ref", "target", "command")
        )

    def steps(group):
        return [(s["id"], [task(t) for t in s["tasks"]]) for s in group]

    out = []
    for n in chain["nodes"]:
        if n["kind"] == "gate":
            out.append((n["id"], "gate", n["artifact"], n["reject_to"], n["chain_finalized"]))
            continue
        loop, base = n["fix_loop"], n["on_base_changed"]
        out.append(
            (
                n["id"],
                "exec",
                (n["policy"] or {}).get("max_attempts"),
                steps(n["steps"]),
                steps(n["on_failure"]["steps"]) if n["on_failure"] else [],
                (
                    steps(loop["steps"]),
                    task(loop["judge"]) if loop["judge"] else None,
                    loop["max_attempts"],
                )
                if loop
                else None,
                base["restart_from"] if base else None,
            )
        )
    return out


@pytest.mark.parametrize("chain", ["default", "quick-task", "ship", "ship-fast"])
def test_resolution_matches_kraft(kl_modules, tmp_path, monkeypatch, chain):
    _, kl_chains = kl_modules
    library = tmp_path / "config"
    shutil.copytree(PLUGIN / "config", library)
    for name, text in EXTENDING.items():
        (library / "chains" / f"{name}.yaml").write_text(textwrap.dedent(text))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    # The packaged chains through the bundled copy itself; the fixtures as a user library.
    env = {} if chain in ("default", "quick-task") else {"KRAFT_CONFIG_DIR": str(library)}

    kraft = _project_kraft(TemplateLibrary.from_yaml_dir(library).resolve_chain(chain))
    lite = kl_chains.resolve(tmp_path, env, chain)

    assert lite["source"] == ("bundled" if not env else "user")
    assert _project_lite(lite) == kraft
