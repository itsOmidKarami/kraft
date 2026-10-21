"""Where Kraft Lite finds a V1 chain, and how it resolves `extends` the way
Kraft does. Libraries are built under `tmp_path` and every lookup gets an
explicit `env`, so no test reads the real home. Kraft's own resolver is the
arbiter of the shapes asserted here: `tests/plugins/test_kraft_lite_parity.py`
resolves the same chains with both."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import kl_chains


def write_library(dir: Path, library: str, chains: dict[str, str]) -> Path:
    """A library at `dir`: `library.yaml` holding `library` (none when it is
    empty) and one `chains/<name>.yaml` per entry, each text dedented."""
    dir.mkdir(parents=True, exist_ok=True)
    if library:
        (dir / "library.yaml").write_text(textwrap.dedent(library))
    if chains:
        (dir / "chains").mkdir(exist_ok=True)
    for name, text in chains.items():
        (dir / "chains" / f"{name}.yaml").write_text(textwrap.dedent(text))
    return dir


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """An empty home, which `Path.home()` answers for the test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


ONE_NODE = """\
    nodes:
      - id: only
        kind: exec
        tasks:
          - id: work
            kind: agent
            prompt: Do the work.
    """

#: Its `tasks:` section comes last, so a test can append entries to it.
LIBRARY = textwrap.dedent(
    """\
    steering:
      tidy:
        instructions: Keep it tidy.
      short:
        instructions: Keep it short.
    tasks:
      worker:
        kind: agent
        harness: claude
        prompt: Do the work.
    """
)

#: A chain `base` with a final gate, and the components it and its children use.
BASE = {
    "base": """\
        id: base
        nodes:
          - id: a
            kind: exec
            tasks:
              - id: work
                extends: worker
          - id: g
            kind: gate
            chain_finalized: true
            reject_to: a
          - id: b
            kind: exec
            tasks:
              - id: work
                extends: worker
        """,
}


@pytest.mark.parametrize(
    ("layout", "instance", "source"),
    [
        pytest.param(("repo",), None, "bundled", id="repo_library_seen_as_repo_namespace"),
        pytest.param(("config", "home"), "config", "user", id="config_dir_beats_home"),
        pytest.param(("templates", "home"), "templates", "user", id="templates_dir_beats_home"),
        pytest.param(("config", "templates"), "config", "user", id="config_dir_beats_templates"),
        pytest.param(("kraft_home", "home"), "kraft_home", "user", id="kraft_home_beats_home"),
        pytest.param(("home",), "home", "user", id="home_config"),
        pytest.param(("bare_config",), None, "bundled", id="config_dir_without_library_is_skipped"),
        pytest.param((), None, "bundled", id="bundled_when_none"),
        pytest.param(("config",), "config", "user", id="user_library_replaces_bundled"),
    ],
)
def test_lookup(tmp_path, home, layout, instance, source):
    root = tmp_path / "repo"
    root.mkdir()
    places = {
        "repo": root / ".kraft",
        "config": tmp_path / "config",
        "bare_config": tmp_path / "config",
        "templates": tmp_path / "templates",
        "kraft_home": tmp_path / "kraft-home" / "config",
        "home": home / ".kraft" / "config",
    }
    env = {
        "KRAFT_CONFIG_DIR": str(places["config"]),
        "KRAFT_TEMPLATES_DIR": str(places["templates"]),
        "KRAFT_HOME": str(tmp_path / "kraft-home"),
    }
    for place in layout:
        if place == "bare_config":
            places[place].mkdir()  # set, but holding no library.yaml
        else:
            write_library(places[place], LIBRARY, {f"{place}-chain": ONE_NODE})

    repo, library = kl_chains.libraries(root, env)
    ids = {entry["id"]: entry["source"] for entry in kl_chains.available(root, env)}

    assert library.name == source
    assert library.path == (places[instance] if instance else kl_chains.BUNDLED)
    assert (repo is not None) == ("repo" in layout)
    if "repo" in layout:
        assert ids["repo:repo-chain"] == "repo"
        assert kl_chains.resolve(root, env, "repo:repo-chain")["id"] == "repo:repo-chain"
    if instance:
        assert ids[f"{instance}-chain"] == "user"
    # A user library replaces the packaged one wholesale: no layering.
    assert ("quick-task" in ids) == (source == "bundled")
    if source != "bundled":
        with pytest.raises(kl_chains.ChainError, match="quick-task"):
            kl_chains.resolve(root, env, "quick-task")


@pytest.mark.parametrize(
    ("repo_chains", "expected"),
    [
        pytest.param({"default": ONE_NODE}, "repo:default", id="repo_default"),
        pytest.param({"hotfix": ONE_NODE}, "default", id="repo_library_without_default"),
        pytest.param(None, "default", id="no_repo_library"),
    ],
)
def test_default_ref(tmp_path, home, repo_chains, expected):
    if repo_chains is not None:
        write_library(tmp_path / ".kraft", "", repo_chains)

    assert kl_chains.default_chain_ref(tmp_path, {}) == expected


#: The bundled `default` chain's nodes, in order.
DEFAULT_IDS = [
    "spec",
    "spec_approval",
    "plan",
    "plan_approval",
    "chain_revision",
    "chain_revision_approval",
    "implementation",
    "verification",
    "work_brief",
    "local_review",
    "describe_merge_request",
    "draft_merge_request",
    "merge_request_feedback",
    "work_item_summary",
    "final_review",
    "mark_ready",
    "external_approval",
    "merge",
    "post_merge_ci",
]


def test_resolve_bundled_default(tmp_path, home):
    chain = kl_chains.resolve(tmp_path, {})
    nodes = {node["id"]: node for node in chain["nodes"]}

    assert (chain["format"], chain["id"], chain["source"]) == ("v1", "default", "bundled")
    assert [node["id"] for node in chain["nodes"]] == DEFAULT_IDS
    verification = nodes["verification"]
    assert [step["id"] for step in verification["steps"]] == ["tests", "review"]
    assert verification["fix_loop"]["max_attempts"] == 2
    assert verification["fix_loop"]["judge"]["components"] == ["strict_judge"]
    assert verification["fix_loop"]["judge"]["path"] == "verification.fix_loop.judge"
    assert nodes["merge_request_feedback"]["on_base_changed"] == {"restart_from": "verification"}
    assert nodes["final_review"]["chain_finalized"] is True
    implement = nodes["implementation"]["steps"][0]["tasks"][0]
    assert implement["components"] == ["implementer"]
    assert implement["path"] == "implementation.main.implement"
    assert (implement["profile"], implement["model"], implement["command"]) == (
        "strong",
        None,
        None,
    )
    assert nodes["spec"]["steps"][0]["tasks"][0]["steering"] == [
        "Keep changes focused. Run the relevant checks before finishing.\n"
    ]


def _first_task(chain: dict) -> dict:
    return chain["nodes"][0]["steps"][0]["tasks"][0]


@pytest.mark.parametrize(
    ("components", "written", "pick", "expected"),
    [
        pytest.param(
            textwrap.dedent(
                """\
            nodes:
              looped:
                kind: exec
                tasks:
                  - {id: work, extends: worker}
                fix_loop:
                  max_attempts: 2
                  tasks:
                    - {id: repair, extends: worker}
            """
            ),
            "{id: only, extends: looped, fix_loop: {max_attempts: 5}}",
            lambda chain: (
                chain["nodes"][0]["fix_loop"]["max_attempts"],
                [t["path"] for t in chain["nodes"][0]["fix_loop"]["steps"][0]["tasks"]],
            ),
            (5, ["only.fix_loop.main.repair"]),
            id="mapping_merges_deep",
        ),
        pytest.param(
            "  steered: {extends: worker, steering: [tidy, short]}\n",
            "{id: only, kind: exec, tasks: [{id: work, extends: steered, steering: [short]}]}",
            lambda chain: (_first_task(chain)["steering"], _first_task(chain)["components"]),
            (["Keep it short."], ["steered", "worker"]),
            id="list_replaces",
        ),
        pytest.param(
            "  skilled: {extends: worker, skill: 'kraft:spec'}\n",
            "{id: only, kind: exec, tasks: [{id: work, extends: skilled, skill: null}]}",
            lambda chain: _first_task(chain)["skill"],
            None,
            id="null_replaces",
        ),
        pytest.param(
            "  routed: {extends: worker, model: m, effort: high}\n",
            "{id: only, kind: exec, tasks: [{id: work, extends: routed, profile: strong}]}",
            lambda chain: tuple(_first_task(chain)[k] for k in ("profile", "model", "effort")),
            ("strong", None, None),
            id="profile_drops_model",
        ),
        pytest.param(
            "  routed: {extends: worker, profile: strong}\n",
            "{id: only, kind: exec, tasks: [{id: work, extends: routed, model: m}]}",
            lambda chain: tuple(_first_task(chain)[k] for k in ("profile", "model", "effort")),
            (None, "m", None),
            id="model_drops_profile",
        ),
        # Kraft splits a subprocess `command` like a shell and runs it without one.
        pytest.param(
            "",
            "{id: only, kind: exec, tasks: [{id: lint, kind: subprocess, "
            "command: \"sh -c 'npm run lint && npx tsc --noEmit'\"}]}",
            lambda chain: _first_task(chain)["command"],
            ["sh", "-c", "npm run lint && npx tsc --noEmit"],
            id="subprocess_command_split_like_a_shell",
        ),
        # Only the key that caps the node's fix loop; the rest Lite cannot enforce.
        pytest.param(
            "",
            "{id: only, kind: exec, tasks: [{id: work, extends: worker}], "
            "policy: {max_attempts: 5, timeout_minutes: 9}, "
            "fix_loop: {tasks: [{id: fix, extends: worker}]}}",
            lambda chain: chain["nodes"][0]["policy"],
            {"max_attempts": 5},
            id="node_policy_max_attempts_carried",
        ),
        pytest.param(
            "",
            "{id: only, kind: exec, tasks: [{id: work, extends: worker}], "
            "policy: {timeout_minutes: 9}}",
            lambda chain: chain["nodes"][0]["policy"],
            None,
            id="node_policy_without_max_attempts",
        ),
    ],
)
def test_merge_rules(tmp_path, home, components, written, pick, expected):
    lib = write_library(tmp_path / "lib", LIBRARY + components, {"c": f"nodes:\n  - {written}\n"})

    chain = kl_chains.resolve(tmp_path, {"KRAFT_CONFIG_DIR": str(lib)}, "c")

    assert pick(chain) == expected


@pytest.mark.parametrize(
    ("chains", "ids", "pick", "expected"),
    [
        pytest.param(
            {"c": "extends: base\nremove: [b]\n"},
            ["a", "g"],
            None,
            None,
            id="remove",
        ),
        pytest.param(
            {"c": "extends: base\noverrides:\n  a.main.work: {prompt: Build it.}\n"},
            ["a", "g", "b"],
            lambda chain: (_first_task(chain)["prompt"], _first_task(chain)["components"]),
            ("Build it.", ["worker"]),
            id="override_path",
        ),
        pytest.param(
            {
                "c": """\
                extends: base
                add:
                  - after: a
                    node: {id: c, kind: exec, tasks: [{id: lint, extends: worker}]}
                """
            },
            ["a", "c", "g", "b"],
            lambda chain: chain["nodes"][1]["steps"][0]["tasks"][0]["path"],
            "c.main.lint",
            id="add_after",
        ),
        pytest.param(
            {
                "mid": "extends: base\nremove: [b]\n",
                "c": "extends: mid\noverrides:\n  a: {tasks: [{id: other, extends: worker}]}\n",
            },
            ["a", "g"],
            lambda chain: _first_task(chain)["path"],
            "a.main.other",
            id="two_levels",
        ),
        pytest.param(
            {
                "c": "extends: base\noverrides:\n"
                "  a: {steps: [{id: s, tasks: [{id: t, extends: worker}]}]}\n"
            },
            ["a", "g", "b"],
            lambda chain: [step["id"] for step in chain["nodes"][0]["steps"]],
            ["s"],
            id="override_swaps_tasks_for_steps",
        ),
    ],
)
def test_chain_extends_chain(tmp_path, home, chains, ids, pick, expected):
    lib = write_library(tmp_path / "lib", LIBRARY, {**BASE, **chains})

    chain = kl_chains.resolve(tmp_path, {"KRAFT_CONFIG_DIR": str(lib)}, "c")

    assert chain["id"] == "c"
    assert [node["id"] for node in chain["nodes"]] == ids
    if pick is not None:
        assert pick(chain) == expected


def _looped(loop: str) -> str:
    """A one-node chain whose fix loop holds `loop` beside its repair task."""
    return (
        "nodes:\n  - {id: a, kind: exec, tasks: [{id: t, extends: worker}], "
        f"fix_loop: {{tasks: [{{id: fix, extends: worker}}], {loop}}}}}\n"
    )


def _task_chain(task: str) -> str:
    return f"nodes:\n  - {{id: a, kind: exec, tasks: [{task}]}}\n"


@pytest.mark.parametrize(
    ("chains", "match"),
    [
        pytest.param(
            {"c": "extends: base\nnodes: []\n"},
            "an extending chain holds no 'nodes'",
            id="nodes_in_extending_chain",
        ),
        pytest.param(
            {"c": "extends: base\nremove: [g]\n"},
            "remove 'g': it is base's final gate",
            id="remove_chain_finalized",
        ),
        pytest.param(
            {"c": "extends: base\noverrides:\n  a: {kind: gate}\n"},
            "overrides.a: an override cannot set kind",
            id="override_kind",
        ),
        pytest.param(
            {"c": "extends: base\noverrides:\n  a.main.work: {chain_finalized: true}\n"},
            "overrides.a.main.work: an override cannot set chain_finalized",
            id="override_chain_finalized",
        ),
        pytest.param(
            {"c": "extends: base\noverrides:\n  a.main.nope: {prompt: x}\n"},
            "overrides.a.main.nope: base has no 'a.main.nope'",
            id="override_unknown_path",
        ),
        pytest.param(
            {"c": "extends: nope\n"}, "extends no chain named 'nope'", id="unknown_parent"
        ),
        pytest.param(
            {"c": "extends: d\n", "d": "extends: c\n"}, "'extends' cycle: c -> d -> c", id="cycle"
        ),
        pytest.param(
            {"c": "extends: team:base\n"},
            "add-on references are not supported by Kraft Lite: team:base",
            id="addon_reference_refused",
        ),
        pytest.param(
            {"c": _task_chain("{id: t, extends: worker, steering: ['team:tidy']}")},
            "add-on references are not supported by Kraft Lite: team:tidy",
            id="addon_steering_refused",
        ),
        pytest.param(
            {"c": _task_chain("{id: t, kind: builtin, ref: kraft.nope}")},
            "a.main.t: unknown builtin ref 'kraft.nope'",
            id="unknown_builtin_ref",
        ),
        pytest.param(
            {"c": _task_chain("{id: t, kind: forge, target: mr.nope}")},
            "a.main.t: unknown forge target 'mr.nope'",
            id="unknown_forge_target",
        ),
        pytest.param(
            {"c": _task_chain("{id: t, extends: nope}")},
            "a.main.t: extends no task named 'nope'",
            id="unknown_component",
        ),
        pytest.param(
            {"c": _task_chain("{id: t, extends: worker, kind: subprocess, command: x}")},
            "a.main.t: extends 'worker' and cannot change kind 'agent' to 'subprocess'",
            id="kind_change",
        ),
        pytest.param(
            {"c": _task_chain("{id: t, extends: tidy}")},
            "extends no task named 'tidy'",
            id="steering_is_not_a_task",
        ),
        pytest.param(
            {"c": _task_chain("{id: t, extends: worker, steering: [nope]}")},
            "a.main.t: selects no steering profile 'nope'",
            id="unknown_steering",
        ),
        pytest.param(
            {
                "c": "nodes:\n  - {id: a, kind: exec, steps: [{id: s, tasks: [{id: t, "
                "extends: worker}], on_failure: {tasks: [{id: fix, extends: worker}]}}]}\n"
            },
            "a.s.on_failure: a step's own on_failure is not supported by Kraft Lite",
            id="step_on_failure",
        ),
        pytest.param(
            {
                "c": _task_chain(
                    "{id: t, extends: worker, on_failure: {tasks: [{id: fix, extends: worker}]}}"
                )
            },
            "a.main.t.on_failure: a task's own on_failure is not supported by Kraft Lite",
            id="task_on_failure",
        ),
        pytest.param(
            {
                "c": "nodes:\n  - {id: a, kind: exec, tasks: [{id: t, extends: worker}], "
                "on_base_changed: {restart_from: a, "
                "on_conflict: {tasks: [{id: fix, extends: worker}]}}}\n"
            },
            "a.on_base_changed.on_conflict: on_conflict is not supported by Kraft Lite",
            id="on_conflict",
        ),
        pytest.param(
            {"c": _looped("judge: {extends: worker}")},
            "a.fix_loop.judge: must be a task with an id",
            id="judge_without_id",
        ),
        pytest.param(
            {"c": _looped("judge: strict")},
            "a.fix_loop.judge: must be a task with an id",
            id="judge_not_a_mapping",
        ),
        *(
            pytest.param(
                {"c": _looped(f"max_attempts: {value}")},
                f"a.fix_loop.max_attempts: must be a whole number above 0, not {shown}",
                id=f"max_attempts_not_positive_int{name}",
            )
            for name, value, shown in (
                ("", "0", "0"),
                # `bool` is an `int` to Python, and `true` would read as 1.
                ("_bool", "true", "True"),
                ("_text", "two", "'two'"),
            )
        ),
        pytest.param(
            {
                "c": "nodes:\n  - {id: a, kind: exec, tasks: [{id: t, extends: worker}], "
                "policy: {max_attempts: 0}}\n"
            },
            "a.policy.max_attempts: must be a whole number above 0, not 0",
            id="node_policy_max_attempts_not_positive_int",
        ),
        pytest.param(
            {
                "c": "nodes:\n  - {id: a, kind: exec, tasks: [{id: t, extends: worker}], "
                "policy: strict}\n"
            },
            "a.policy: must be a mapping",
            id="node_policy_not_a_mapping",
        ),
        # Kraft stops the item naming a command it cannot split; Lite stops at resolve.
        pytest.param(
            {"c": _task_chain('{id: t, kind: subprocess, command: "sh -c \'oops"}')},
            'a.main.t: cannot split command "sh -c \'oops": No closing quotation',
            id="subprocess_command_unsplittable",
        ),
        pytest.param(
            {"c": _task_chain("{id: t, kind: subprocess, command: '  '}")},
            "a.main.t: a subprocess task needs its 'command'",
            id="subprocess_command_blank",
        ),
    ],
)
def test_chain_extension_refusals(tmp_path, home, chains, match):
    lib = write_library(tmp_path / "lib", LIBRARY, {**BASE, **chains})

    with pytest.raises(kl_chains.ChainError, match=f"^kraft-lite: .*{match}") as refused:
        kl_chains.resolve(tmp_path, {"KRAFT_CONFIG_DIR": str(lib)}, "c")

    assert str(lib / "chains" / "c.yaml") in str(refused.value)


def test_component_cycle_is_refused(tmp_path, home):
    library = LIBRARY + "  loop_a: {extends: loop_b}\n  loop_b: {extends: loop_a}\n"
    lib = write_library(tmp_path / "lib", library, {"c": _task_chain("{id: t, extends: loop_a}")})

    with pytest.raises(kl_chains.ChainError, match="'extends' cycle: loop_a -> loop_b -> loop_a"):
        kl_chains.resolve(tmp_path, {"KRAFT_CONFIG_DIR": str(lib)}, "c")


@pytest.mark.parametrize(
    ("written", "components"),
    [
        pytest.param("{id: t, extends: own}", ["own"], id="bare_name_is_its_own"),
        pytest.param("{id: t, extends: 'library:own'}", ["own"], id="library_qualifier"),
    ],
)
def test_repo_library_names_its_own(tmp_path, home, written, components):
    """In the repo library, bare names and `library:<name>` are its own
    entries (`repo-libraries.md`); the instance's `worker` is not reachable."""
    repo_lib = "tasks:\n  own: {kind: agent, prompt: Mine., steering: ['library:mine']}\n"
    repo_lib += "steering:\n  mine: {instructions: Repo rules.}\n"
    write_library(tmp_path / ".kraft", repo_lib, {"ship": _task_chain(written)})
    write_library(tmp_path / ".kraft", "", {"bad": _task_chain("{id: t, extends: worker}")})

    chain = kl_chains.resolve(tmp_path, {}, "repo:ship")

    assert (chain["id"], chain["source"]) == ("repo:ship", "repo")
    assert _first_task(chain)["components"] == components
    assert _first_task(chain)["steering"] == ["Repo rules."]
    with pytest.raises(kl_chains.ChainError, match="extends no task named 'worker'"):
        kl_chains.resolve(tmp_path, {}, "repo:bad")


def test_resolve_path_argument(tmp_path, home):
    """A chain file outside any library resolves its `extends` against the
    instance library, here the bundled one."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    file = elsewhere / "mine.yaml"
    file.write_text("extends: quick-task\nremove: [verify]\n")

    chain = kl_chains.resolve(tmp_path, {}, path=file)

    assert (chain["id"], chain["source"]) == ("mine", str(file))
    assert [node["id"] for node in chain["nodes"]] == ["implementation"]
    assert _first_task(chain)["components"] == ["implementer"]


#: A chain whose gate on the plan sends a rejection back to the spec's author.
CROSSED = """\
    nodes:
      - id: author
        kind: exec
        tasks:
          - {id: write, extends: worker, produces: spec}
      - id: planner
        kind: exec
        tasks:
          - {id: write, extends: worker, produces: plan}
      - id: check
        kind: gate
        artifact: plan
        reject_to: author
      - id: build
        kind: exec
        tasks:
          - {id: work, extends: worker}
    """


#: A chain whose last node restarts, on a base change, from the plan's author.
RESTARTING = """\
    nodes:
      - id: author
        kind: exec
        tasks:
          - {id: write, extends: worker, produces: spec}
      - id: planner
        kind: exec
        tasks:
          - {id: write, extends: worker, produces: plan}
      - id: build
        kind: exec
        tasks:
          - {id: work, extends: worker}
      - id: ship
        kind: exec
        tasks:
          - {id: work, extends: worker}
        on_base_changed: {restart_from: planner}
    """


def _drop(*dropped: str) -> list[str]:
    return [n for n in DEFAULT_IDS if n not in dropped]


@pytest.mark.parametrize(
    ("chain", "kinds", "kept"),
    [
        pytest.param("default", set(), DEFAULT_IDS, id="nothing_attached"),
        pytest.param("default", {"spec"}, _drop("spec", "spec_approval"), id="spec"),
        pytest.param(
            "default",
            {"spec", "plan"},
            _drop("spec", "spec_approval", "plan", "plan_approval"),
            id="spec_and_plan",
        ),
        # A `chain_finalized` gate is never dropped, whatever its artifact says.
        pytest.param("default", {"review_brief"}, _drop("work_item_summary"), id="final_gate_kept"),
        # The gate survives; its `reject_to` named a dropped node, so the target goes.
        pytest.param(
            "crossed", {"spec"}, ["planner", "check->None", "build"], id="reject_to_cleared"
        ),
        pytest.param("crossed", {"plan"}, ["author", "build"], id="node_and_gate"),
        # A `restart_from` naming a dropped node moves to the nearest kept exec
        # node before it, else the first kept one, as a child chain's does.
        pytest.param(
            "restarting",
            {"plan"},
            ["author", "build", "ship~author"],
            id="restart_from_redirected_to_an_earlier_node",
        ),
        pytest.param(
            "restarting",
            {"spec", "plan"},
            ["build", "ship~build"],
            id="restart_from_redirected_to_the_first_node",
        ),
        pytest.param("restarting", {"spec"}, ["planner", "build", "ship~planner"], id="kept"),
    ],
)
def test_trim(tmp_path, home, chain, kinds, kept):
    lib = write_library(tmp_path / "lib", LIBRARY, {"crossed": CROSSED, "restarting": RESTARTING})
    env = {} if chain == "default" else {"KRAFT_CONFIG_DIR": str(lib)}
    resolved = kl_chains.resolve(tmp_path, env, chain)

    trimmed = kl_chains.trim(resolved, kinds)

    def show(n: dict) -> str:
        if n["kind"] == "gate" and chain == "crossed":
            return f"{n['id']}->{n['reject_to']}"
        if chain == "restarting" and n["on_base_changed"]:
            return f"{n['id']}~{n['on_base_changed']['restart_from']}"
        return n["id"]

    shown = [show(n) for n in trimmed["nodes"]]
    assert shown == kept
    assert dict(trimmed, nodes=None) == dict(resolved, nodes=None)


def test_trim_refuses_to_empty_a_chain():
    gate = {"id": "g", "kind": "gate", "artifact": "spec", "reject_to": None}
    chain = {"format": "v1", "id": "c", "source": "s", "nodes": [dict(gate, chain_finalized=False)]}

    with pytest.raises(kl_chains.ChainError, match="would leave chain 'c' with no nodes"):
        kl_chains.trim(chain, {"spec"})
