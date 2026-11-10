"""Kraft Lite against Kraft: the plugin's bundled copy of the packaged library is
the repo's own, and its dependency-free YAML reader agrees with `yaml.safe_load`
on every config file Kraft ships, and its `## Slices` reader reads a plan as
`kraft.slices` does. The plugin's own tests cannot check these: the directory is
published as its own repo, with no `config/` above it, no PyYAML and no Kraft."""

from __future__ import annotations

import importlib.util
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from kraft import progress as kraft_progress
from kraft import slices as kraft_slices
from kraft.addons import builtin, load
from kraft.slices import SliceRefusal, parse_slices
from kraft.templates import models
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


@pytest.mark.parametrize(
    ("bundle", "sources"),
    [
        pytest.param(
            "config",
            {"library.yaml": CONFIG / "library.yaml"}
            | {"chains/" + p.name: p for p in (CONFIG / "chains").glob("*.yaml")},
            id="library",
        ),
        pytest.param(
            "methods",
            {p.parent.name + ".md": p for p in (REPO / "src/kraft/skills").glob("*/SKILL.md")},
            id="worker_methods",
        ),
    ],
)
def test_the_bundled_copy_is_the_packaged_one(bundle, sources):
    bundled = {
        p.relative_to(PLUGIN / bundle).as_posix()
        for p in (PLUGIN / bundle).rglob("*")
        if p.is_file()
    }

    assert bundled == set(sources), "run: just kraft-lite-config"
    for rel, source in sorted(sources.items()):
        assert (PLUGIN / bundle / rel).read_bytes() == source.read_bytes(), (
            f"{bundle}/{rel} differs; run: just kraft-lite-config"
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


#: Chains that extend chains, two levels deep, over the packaged library.
EXTENDING = {
    "ship": """\
        extends: default
        remove: [external_approval]
        overrides:
          implementation.main.implement: {prompt: Build it.}
          local_review: {artifact_required: true}
          verification.fix_loop.judge: {prompt: Be strict.}
          merge_request_feedback.on_failure.repair: {tasks: [{id: fix, extends: repair_mr_checks}]}
          merge.on_merge_rejected.repair.main.repair: {prompt: Fix the queue's failure.}
        add:
          - after: work_brief
            node:
              id: lint
              kind: exec
              tasks: [{id: run, extends: verify_changed_scopes, suite: full}]
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
            *(
                getattr(getattr(t.task, key, None), "value", None)
                for key in ("ref", "target", "suite")
            ),
            # What Kraft runs (`executor.dispatch`): the string split like a shell.
            shlex.split(command) if command is not None else None,
        )

    def steps(group):
        return [(s.id, [task(t) for t in s.tasks]) for s in group]

    out = []
    for n in resolved.nodes:
        if n.node.kind.value == "gate":
            node = n.node
            out.append(
                (
                    n.id,
                    "gate",
                    node.artifact,
                    node.reject_to,
                    node.chain_finalized,
                    node.artifact_required,
                )
            )
            continue
        loop = n.node.fix_loop
        base = n.node.on_base_changed
        rejected = n.node.on_merge_rejected
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
                (rejected.restart_from, steps(n.on_merge_rejected)) if rejected else None,
            )
        )
    return out


def _project_lite(chain: dict) -> list:
    """`kl_chains.resolve`'s answer, reduced the same way."""

    def task(t):
        keys = ("path", "kind", "skill", "prompt", "produces", "ref", "target")
        # Kraft's model writes the default `suite` onto every builtin task; Lite leaves it unset.
        suite = t["suite"] or ("targeted" if t["kind"] == "builtin" else None)
        return (*(t[key] for key in keys), suite, t["command"])

    def steps(group):
        return [(s["id"], [task(t) for t in s["tasks"]]) for s in group]

    out = []
    for n in chain["nodes"]:
        if n["kind"] == "gate":
            out.append(
                (
                    n["id"],
                    "gate",
                    n["artifact"],
                    n["reject_to"],
                    n["chain_finalized"],
                    n["artifact_required"],
                )
            )
            continue
        loop, base, rejected = n["fix_loop"], n["on_base_changed"], n["on_merge_rejected"]
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
                (rejected["restart_from"], steps(rejected["repair"]["steps"]))
                if rejected
                else None,
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


def test_lite_knows_every_key_of_kraft_template_models(kl_modules):
    """A key Kraft's chain models gain is one Lite reads, reads past or refuses
    on purpose, never one it drops unseen; and the names Lite copied of
    Kraft's enums and handler models still match them."""
    _, kl_chains = kl_modules
    for name, keys in kl_chains.MODEL_KEYS.items():
        declared = [key for group in keys.values() for key in group]
        assert len(declared) == len(set(declared)), f"{name}: a key in two groups"
        assert set(declared) == set(getattr(models, name).model_fields), name
    assert set(kl_chains.MODEL_KEYS) == {
        *("AgentTask", "ForgeTask", "BuiltinTask", "SubprocessTask"),
        *("ExecNode", "GateNode", "Step", "FixLoop"),
    }

    assert kl_chains.BUILTIN_REFS == tuple(models.BuiltinAction)
    assert kl_chains.FORGE_TARGETS == tuple(
        a for a in models.ForgeAction if a is not models.ForgeAction.CI_PIPELINE
    )
    assert kl_chains.SUITES == tuple(models.TestSuite)
    assert kl_chains.HANDLER_KEYS == {
        "on_base_changed": tuple(models.BaseChangePolicy.model_fields),
        "on_merge_rejected": tuple(models.MergeRejectionPolicy.model_fields),
    }


#: A chain of your own over the built-in add-on, a node of yours from one of its components.
OVER_THE_BUILTIN = """\
    extends: kraft-default:default
    remove: [external_approval]
    add:
      - after: work_brief
        node:
          id: lint
          kind: exec
          tasks: [{id: run, extends: kraft-default:verify_changed_scopes}]
    """


@pytest.mark.parametrize("chain", ["default", "quick-task", "ship"])
def test_a_seeded_home_resolves_as_kraft_resolves_it(kl_modules, tmp_path, monkeypatch, chain):
    """A home Kraft seeds today is thin chains over its built-in add-on, which
    is the library Lite bundles: Lite reads it, as the README says it reads the
    same files Kraft reads."""
    _, kl_chains = kl_modules
    monkeypatch.setattr(builtin, "source_dir", lambda: CONFIG)
    home = tmp_path / "config"
    builtin.write_thin_seed(home)
    (home / "chains" / "ship.yaml").write_text(textwrap.dedent(OVER_THE_BUILTIN))
    addons = load.installed(home, tmp_path / "run" / "addons")

    library = TemplateLibrary.from_yaml_dir(home, skills_dir=None, addons=addons)
    kraft = _project_kraft(library.resolve_chain(chain))
    lite = kl_chains.resolve(tmp_path, {"KRAFT_CONFIG_DIR": str(home)}, chain)

    assert _project_lite(lite) == kraft


# --- a plan's `## Slices` section --------------------------------------------------

_SPEC = importlib.util.spec_from_file_location("kraft_lite_kl_slices", PLUGIN / "kl_slices.py")
kl_slices = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(kl_slices)
#: Lite has no cap on slices: Kraft's `max_slices` is policy, not grammar.
NO_CAP = 99
TASKS = "".join(f"### Task {n}: Step {n}\n" for n in range(1, 31))


def _plan(section: str, tasks: str = TASKS) -> str:
    return f"# Plan\n\n{tasks}\n## Slices\n\n{section}\n"


def _kraft_slices(text: str):
    """Kraft's split as keys, titles, tasks and prelude; or its refusal's reason; or None."""
    split = parse_slices(text, max_slices=NO_CAP)
    if split is None or isinstance(split, SliceRefusal):
        return split and split.reason
    return {
        "slices": [(s.key, s.title, list(s.tasks)) for s in split.slices],
        "prelude": list(split.prelude),
    }


def _lite_slices(text: str):
    """Lite's split in the same terms as `_kraft_slices`."""
    split = kl_slices.parse_slices(text)
    if not isinstance(split, dict):
        return split
    return {
        "slices": [(s["key"], s["title"], s["tasks"]) for s in split["slices"]],
        "prelude": split["prelude"],
    }


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("# Plan\n\n### Task 1: A\n", id="no_section"),
        pytest.param(_plan("- **Slice 1: A.** Tasks 1-2.\n- **Slice 2: B.** Task 3."), id="split"),
        pytest.param(
            _plan("- **Slice 1: A.** Tasks 2–4.\n* **Slice 2: B** Task 6"), id="en_dash_and_star"
        ),
        pytest.param(
            _plan(
                "- **Slice 1: A.** Task 1.\n- **Slice 2: B.** Task 2.",
                "### Task 1: x [DONE]\n### Task 2: y\n### Task 3: z\n",
            ),
            id="done_task_in_a_slice",
        ),
        pytest.param(
            _plan(
                "- **Slice 1: A.** Task 2.\n- **Slice 2: B.** Task 3.",
                "### Task 1: x [DONE]\n### Task 2: y\n### Task 3: z\n### Task 4: w\n",
            ),
            id="done_task_is_no_prelude",
        ),
        pytest.param(
            "# Plan\n\n```\n## Slices\n- **Slice 1: A.** Task 1.\n```\n\n### Task 1: x\n",
            id="fenced_section_is_none",
        ),
        pytest.param(
            _plan("- **Slice 1: A.** Tasks 1-2.\n- **Slice 2: B.** Tasks 2-3."), id="task_twice"
        ),
        pytest.param(
            _plan("- **Slice 1: A.** Task 1.\n- **Slice 2: B.** Task 31."), id="unknown_task"
        ),
        pytest.param(_plan("- **Slice 1: A.** Task 1.\n- **Slice 3: B.** Task 2."), id="key_gap"),
        pytest.param(_plan("- **Slice 1: A.** Tasks 1-3."), id="one_slice"),
        pytest.param(
            _plan("- **Slice 1: A.** Tasks 3-1.\n- **Slice 2: B.** Task 4."), id="backwards"
        ),
        pytest.param(_plan("- **Slice 1:** Task 1.\n- **Slice 2: B.** Task 4."), id="no_title"),
        pytest.param(_plan("- **Slice 1: A.**\n- **Slice 2: B.** Task 4."), id="no_task"),
        pytest.param(_plan("- Slice one: tasks 1 and 2"), id="not_a_slice_bullet"),
    ],
)
def test_kraft_and_lite_read_a_slices_section_alike(text):
    assert _lite_slices(text) == _kraft_slices(text)


@pytest.mark.parametrize(
    "tail",
    [
        pytest.param("Task 2. Depends on slice 1.", id="depends_on"),
        pytest.param("Task 2. Needs slice 1.", id="needs_slice"),
        pytest.param("Task 3, after slice 1", id="after_slice"),
        # Lite reads `Comes after` only as the last sentence, so its reason differs.
        pytest.param("Comes after slice 1, then Task 2.", id="comes_after_mid_text"),
    ],
)
def test_kraft_and_lite_both_refuse_a_dependency_in_other_words(tail):
    """Read as task numbers, the `1` in any of these would be a task: both refuse."""
    text = _plan(f"- **Slice 1: A.** Task 1.\n- **Slice 2: B.** {tail}")

    assert isinstance(_kraft_slices(text), str) and isinstance(_lite_slices(text), str)
    assert "comes after" in _kraft_slices(text) and "comes after" in _lite_slices(text)


@pytest.mark.parametrize(
    "name, kraft",
    [
        pytest.param(name, module, id=name)
        for module, names in (
            (kraft_slices, ("_HEADING", "_SLICES_HEADING", "_BULLET", "_SLICE_BOLD")),
            (kraft_slices, ("_COMES_AFTER", "_TASK_ITEM")),
            (kraft_progress, ("_TASK_HEADING", "_FENCE")),
        )
        for name in names
    ],
)
def test_lites_copied_regexes_are_krafts(name, kraft):
    """The grammar is copied, not imported: Lite ships without Kraft."""
    ours, theirs = getattr(kl_slices, name), getattr(kraft, name)

    assert (ours.pattern, ours.flags) == (theirs.pattern, theirs.flags)


def _documented_examples() -> list:
    """Every `## Slices` example Lite documents: a fenced block holding the
    heading, or an inline bullet beside a filler bullet for each other key."""
    out = []
    for path in (
        PLUGIN / "README.md",
        PLUGIN / "skills" / "run-slices" / "SKILL.md",
        REPO / "docsite" / "content" / "3.guides" / "5.kraft-lite.md",
    ):
        text = path.read_text()
        for n, block in enumerate(re.findall(r"```markdown\n(.*?)```", text, re.S)):
            if "## Slices" in block:
                out.append(pytest.param(block.split("## Slices", 1)[1], id=f"{path.name}:block{n}"))
        for bullet in re.findall(r"`(- \*\*Slice \d+:[^`]*)`", text):
            key = int(re.match(r"- \*\*Slice (\d+)", bullet)[1])
            lines = [
                bullet if k == key else f"- **Slice {k}: Filler.** Task {20 + k}."
                for k in range(1, max(key, 2) + 1)
            ]
            out.append(pytest.param("\n".join(lines), id=f"{path.name}:{bullet}"))
    return out


DOCUMENTED = _documented_examples()


def test_lite_documents_its_slices_grammar():
    """The examples are extracted, so one that stops matching the extraction
    must not pass by vanishing."""
    assert {p.id.split(":")[0] for p in DOCUMENTED} == {"README.md", "SKILL.md", "5.kraft-lite.md"}
    assert sum("Comes after" in p.values[0] for p in DOCUMENTED) == 2


@pytest.mark.parametrize("section", DOCUMENTED)
def test_lites_documented_slices_read_as_kraft_reads_them(section):
    """Kraft's grammar reads the same in both. Lite's `after` is the one
    difference: Kraft refuses it, and never misreads it as task numbers."""
    text = _plan(section)
    lite = _lite_slices(text)
    if "Comes after" in section:
        assert "comes after another" in _kraft_slices(text)
        assert isinstance(lite, dict) and lite["slices"][-1][2] == [6]
        assert kl_slices.parse_slices(text)["slices"][-1]["after"] == [1]
    else:
        assert isinstance(lite, dict) and lite == _kraft_slices(text)
