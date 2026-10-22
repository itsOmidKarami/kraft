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
        # A set directory is the user library, as Kraft's `paths.config_dir`: one
        # without library.yaml is what Kraft seeds from the packaged files.
        pytest.param(
            ("bare_config", "home"),
            None,
            "bundled",
            id="config_dir_without_library_uses_bundled_not_home",
        ),
        pytest.param(
            ("bare_kraft_home", "home"),
            None,
            "bundled",
            id="kraft_home_without_library_uses_bundled_not_home",
        ),
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
        "bare_kraft_home": tmp_path / "kraft-home" / "config",
        "home": home / ".kraft" / "config",
    }
    # Each variable is set only when the layout has its place.
    names = {
        "KRAFT_CONFIG_DIR": ("config", "bare_config"),
        "KRAFT_TEMPLATES_DIR": ("templates",),
        "KRAFT_HOME": ("kraft_home", "bare_kraft_home"),
    }
    env = {
        name: str(places[place].parent if name == "KRAFT_HOME" else places[place])
        for name, owners in names.items()
        for place in owners
        if place in layout
    }
    for place in layout:
        if place.startswith("bare_"):
            places[place].mkdir(parents=True)  # set, but holding no library.yaml
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

    repo, _ = kl_chains.libraries(tmp_path, {})
    assert kl_chains.default_chain_ref(repo) == expected


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
    rejected = nodes["merge"]["on_merge_rejected"]
    assert rejected["restart_from"] == "merge_request_feedback"
    [[repair]] = [step["tasks"] for step in rejected["repair"]["steps"]]
    assert (repair["path"], repair["components"]) == (
        "merge.on_merge_rejected.repair.main.repair",
        ["repair_merge_queue_failure"],
    )
    rejecting = [n["id"] for n in chain["nodes"] if n.get("on_merge_rejected")]
    assert rejecting == ["merge"]
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


#: The node `_rejecting` adds last: one merge task, and what it does on a drop.
MERGE_NODE = "{id: m, kind: exec, tasks: [%s], %s}"
MERGE_TASK = "{id: merge, kind: forge, target: mr.merge}"
REPAIR = "repair: {tasks: [{id: fix, extends: worker}]}"


def _rejecting(rejected: str, before: str = "", tasks: str = MERGE_TASK, extra: str = "") -> str:
    """A chain of exec node `a`, then the nodes in `before`, then a merge node `m`
    whose `on_merge_rejected` holds `rejected` (and `extra` beside it)."""
    node = MERGE_NODE % (tasks, f"on_merge_rejected: {{{rejected}}}{extra}")
    return (
        "nodes:\n  - {id: a, kind: exec, tasks: [{id: t, extends: worker}]}\n"
        f"{before}  - {node}\n"
    )


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
        # Kraft refuses a node whose own tasks disagree: trim could not drop it cleanly.
        pytest.param(
            {
                "c": _task_chain(
                    "{id: s, extends: worker, produces: spec}, {id: t, extends: worker}"
                )
            },
            r"a: node 'a' does not agree on what it produces \(\['\(none\)', 'spec'\]\); "
            r"a node either wholly produces one kind or declares none",
            id="produces_some_and_none",
        ),
        pytest.param(
            {
                "c": _task_chain(
                    "{id: s, extends: worker, produces: spec}, "
                    "{id: p, extends: worker, produces: plan}"
                )
            },
            r"a: node 'a' does not agree on what it produces \(\['plan', 'spec'\]\); "
            r"a node either wholly produces one kind or declares none",
            id="produces_two_kinds",
        ),
        # Kraft's `Chain`: a merge rejection rewinds strictly backward, through the
        # final gate when the merge comes after it, from the node that merges.
        pytest.param(
            {"c": _rejecting(f"restart_from: m, {REPAIR}")},
            "m: on_merge_rejected.restart_from 'm' must name an earlier execution node",
            id="merge_rejected_restart_from_itself",
        ),
        pytest.param(
            {"c": _rejecting(f"restart_from: g, {REPAIR}", "  - {id: g, kind: gate}\n")},
            "m: on_merge_rejected.restart_from 'g' must name an earlier execution node",
            id="merge_rejected_restart_from_a_gate",
        ),
        pytest.param(
            {
                "c": _rejecting(
                    f"restart_from: b, {REPAIR}",
                    "  - {id: g, kind: gate, chain_finalized: true}\n"
                    "  - {id: b, kind: exec, tasks: [{id: t, extends: worker}]}\n",
                )
            },
            "m: on_merge_rejected.restart_from 'b' must name an execution node before the "
            "final gate 'g'",
            id="merge_rejected_restart_from_past_the_final_gate",
        ),
        pytest.param(
            {"c": _rejecting(f"restart_from: a, {REPAIR}", tasks="{id: t, extends: worker}")},
            "m: on_merge_rejected needs an mr.merge task in the same node",
            id="merge_rejected_without_mr_merge",
        ),
        # A merge in the repair is not the merge the forge rejects.
        pytest.param(
            {
                "c": _rejecting(
                    f"restart_from: a, repair: {{tasks: [{MERGE_TASK}]}}",
                    tasks="{id: t, extends: worker}",
                )
            },
            "m: on_merge_rejected needs an mr.merge task in the same node",
            id="merge_rejected_merge_only_in_its_repair",
        ),
        pytest.param(
            {
                "c": _rejecting(
                    f"restart_from: a, {REPAIR}",
                    extra=", fix_loop: {tasks: [{id: fix, extends: worker}]}",
                )
            },
            "m: on_merge_rejected cannot share a node with fix_loop",
            id="merge_rejected_beside_a_fix_loop",
        ),
        pytest.param(
            {
                "c": _rejecting(
                    f"restart_from: a, {REPAIR}",
                    extra=", on_failure: {tasks: [{id: fix, extends: worker}]}",
                )
            },
            "m: on_merge_rejected cannot share a node with on_failure",
            id="merge_rejected_beside_on_failure",
        ),
        pytest.param(
            {"c": _rejecting("restart_from: a")},
            "m.on_merge_rejected.repair: must be a mapping",
            id="merge_rejected_without_repair",
        ),
        pytest.param(
            {"c": _rejecting(f"restart_from: a, {REPAIR}, max_attempts: 2")},
            r"m.on_merge_rejected.max_attempts: unknown key; known: \['restart_from', 'repair'\]",
            id="merge_rejected_unknown_key",
        ),
        pytest.param(
            {
                "c": "nodes:\n  - {id: a, kind: exec, tasks: [{id: t, extends: worker}], "
                "on_base_changed: {restart_form: a}}\n"
            },
            r"a.on_base_changed.restart_form: unknown key; known: \[.restart_from.",
            id="base_changed_unknown_key",
        ),
        pytest.param(
            {
                "r": _rejecting(
                    f"restart_from: b, {REPAIR}",
                    "  - {id: b, kind: exec, tasks: [{id: t, extends: worker}]}\n",
                ),
                "c": "extends: r\nremove: [b]\n",
            },
            "m: its on_merge_rejected.restart_from names 'b', which this chain removes",
            id="merge_rejected_restart_from_removed",
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


@pytest.mark.parametrize(
    ("folder", "edit", "nodes", "components", "id_and_source"),
    [
        pytest.param(
            "elsewhere",
            "remove: [verify]\n",
            ["implementation"],
            ["implementer"],
            ("mine", "{file}"),
            id="outside",
        ),
        # A draft beside the repo library is not one of its chains.
        pytest.param(
            ".kraft/drafts",
            "remove: [verify]\n",
            ["implementation"],
            ["implementer"],
            ("mine", "{file}"),
            id="beside_the_repo_library",
        ),
        # One of the repo library's own chains: that chain, as `repo:mine`.
        pytest.param(".kraft/chains", "", ["a"], ["own"], ("repo:mine", "repo"), id="repo_chain"),
    ],
)
def test_resolve_path_argument(tmp_path, home, folder, edit, nodes, components, id_and_source):
    """A chain file in the repo library's `chains/` is that repo chain; any
    other resolves its `extends` against the instance library, here the
    bundled one. The repo has a `quick-task` of its own, so the two cannot
    agree by accident."""
    repo_lib = "tasks:\n  own: {kind: agent, prompt: Mine.}\n"
    write_library(
        tmp_path / ".kraft", repo_lib, {"quick-task": _task_chain("{id: t, extends: own}")}
    )
    file = tmp_path / folder / "mine.yaml"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f"extends: quick-task\n{edit}")

    chain = kl_chains.resolve(tmp_path, {}, path=file)

    id, source = id_and_source
    assert (chain["id"], chain["source"]) == (id, source.format(file=file))
    assert [node["id"] for node in chain["nodes"]] == nodes
    assert _first_task(chain)["components"] == components
