"""Per-scope time caps, as the schema holds them (Rulings 194, 195, 196): a
child's cap may not exceed its parent's, refused at load and when an item is
filed or its override set, naming both scopes; a work item's own cap only
tightens; a gate's `timeout` sits under its total cap; and the retired
`wait_timeout_minutes` / `wait: timeout` still read, as the caps that replaced
them. What the caps do at runtime is tests/executor/test_time_caps.py."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import yaml

from kraft import policy as _policy
from kraft.policy import InstancePolicy, InstancePolicyInput, PolicyError
from kraft.templates.environment import WorkItemTarget
from kraft.templates.library import CHAINS_DIR, LIBRARY_FILE, TemplateLibrary
from kraft.templates.models import Chain, MaterializedChain, ResolvedChain

FIELDS = ("time_cap_minutes", "total_time_cap_minutes")


def _sub(task_id: str, **fields) -> dict:
    return {"id": task_id, "kind": "subprocess", "command": "true", **fields}


def _nodes(*, chain=None, node=None, step=None, task=None, handler=None) -> dict:
    """One node `build`, one step `run`, one task `impl` with a recovery
    task `fix` under it -- each given `policy:` when its argument is set."""

    def own(policy):
        return {"policy": policy} if policy else {}

    impl = _sub("impl", **own(task), on_failure={"tasks": [_sub("fix", **own(handler))]})
    return {
        "id": "c",
        **own(chain),
        "nodes": [
            {
                "id": "build",
                "kind": "exec",
                **own(node),
                "steps": [{"id": "run", "tasks": [impl], **own(step)}],
            },
            {"id": "review", "kind": "gate"},
        ],
    }


def _every_level(**caps) -> dict:
    """The same `defaults:`/`maxima:` caps at every level (Ruling 211)."""
    return {level: caps for level in _policy.CAP_LEVELS}


def _materialize(chain: dict, **policy_yaml) -> MaterializedChain:
    return ResolvedChain.from_chain(Chain.model_validate(chain)).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput.model_validate(policy_yaml)),
    )


# ── a child's cap cannot exceed its parent's ──────────────────────────────────


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize(
    ("scopes", "message"),
    [
        (
            {"step": 10, "task": 20},
            "task build.run.impl sets {f} 20 > its step build.run's 10",
        ),
        ({"node": 10, "step": 20}, "step build.run sets {f} 20 > its node build's 10"),
        ({"chain": 10, "node": 20}, "node build sets {f} 20 > the chain's 10"),
        ({"node": 10, "task": 20}, "task build.run.impl sets {f} 20 > its step build.run's 10"),
        (
            {"task": 10, "handler": 20},
            "task build.run.impl.on_failure.main.fix sets {f} 20 > its step "
            "build.run.impl.on_failure.main's 10",
        ),
    ],
    ids=[
        "task-over-step",
        "step-over-node",
        "node-over-chain",
        "task-over-node",
        "handler-over-task",
    ],
)
def test_a_child_cap_above_its_parents_is_refused_when_the_item_is_filed(field, scopes, message):
    """Ruling 194: "if a step has 10 mins, the task inside that step can't set
    it to 20". The refusal names both scopes."""
    chain = _nodes(**{scope: {field: minutes} for scope, minutes in scopes.items()})

    with pytest.raises(PolicyError) as refused:
        _materialize(chain)

    assert message.format(f=field) in str(refused.value)
    assert refused.value.field == field


@pytest.mark.parametrize("field", FIELDS)
def test_a_child_cap_above_its_parents_is_refused_at_load(tmp_path, field):
    """`TemplateLibrary.lint`: a chain that could never be filed is reported
    when the library loads, naming both scopes, not when the first item hits
    it."""
    (tmp_path / CHAINS_DIR).mkdir()
    (tmp_path / LIBRARY_FILE).write_text("{}\n")
    chain = _nodes(step={field: 10}, task={field: 20})
    (tmp_path / CHAINS_DIR / "c.yaml").write_text(yaml.safe_dump(chain))

    (issue,) = TemplateLibrary.from_yaml_dir(tmp_path).lint()

    assert f"task build.run.impl sets {field} 20 > its step build.run's 10" in issue.message


@pytest.mark.parametrize("field", FIELDS)
def test_a_child_cap_at_or_below_its_parents_is_accepted_and_each_scope_keeps_its_own(field):
    chain = _materialize(_nodes(node={field: 30}, step={field: 30}, task={field: 5}))

    by_path = {t.path: t for n in chain.chain.nodes for t in n.tasks()}
    assert getattr(chain.policy_for(by_path["build.run.impl"]), field) == 5
    assert getattr(chain.policy_for(chain.chain.nodes[0]), field) == 30
    # A recovery task inherits the task it recovers.
    assert getattr(chain.policy_for(by_path["build.run.impl.on_failure.main.fix"]), field) == 5


@pytest.mark.parametrize("field", FIELDS)
def test_no_scope_may_set_a_cap_past_the_administrator_maximum(field):
    with pytest.raises(PolicyError, match="administrator maximum 60|> its"):
        _materialize(_nodes(task={field: 90}), maxima={"tasks": {field: 60}})


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("scope", ["chain", "node", "task"])
def test_a_scope_may_raise_a_cap_above_the_default_up_to_the_maximum(field, scope):
    """Ruling 198: an instance `defaults:` cap is a default, not a ceiling.
    Any scope may set a longer one, up to `maxima:`; a default applies only
    where nothing set one."""
    chain = _materialize(
        _nodes(**{scope: {field: 240}}),
        defaults=_every_level(**{field: 30}),
        maxima={"work_item": {field: 240}},
    )
    by_path = {t.path: t for n in chain.chain.nodes for t in n.tasks()}

    assert getattr(chain.policy_for(by_path["build.run.impl"]), field) == 240
    # The gate beside it sets nothing: it keeps the default, or the chain's.
    expected = 240 if scope == "chain" else 30
    assert getattr(chain.policy_for(chain.chain.nodes[1]), field) == expected


@pytest.mark.parametrize("field", FIELDS)
def test_a_scope_past_the_maximum_is_refused_even_with_no_parent_cap(field):
    with pytest.raises(PolicyError, match="administrator maximum 240"):
        _materialize(
            _nodes(node={field: 300}),
            defaults=_every_level(**{field: 30}),
            maxima={"work_item": {field: 240}},
        )


@pytest.mark.parametrize("field", FIELDS)
def test_a_default_cap_past_its_maximum_is_refused_when_policy_loads(field):
    with pytest.raises(
        ValueError, match=f"defaults.tasks.{field} 90 exceeds maxima.work_item.{field} 60"
    ):
        InstancePolicyInput.model_validate(
            {"defaults": {"tasks": {field: 90}}, "maxima": {"work_item": {field: 60}}}
        )


def test_a_gate_timeout_past_its_total_cap_is_refused():
    """Ruling 195: a gate's own timeout sits under the total caps around it."""
    chain = _nodes(chain={"total_time_cap_minutes": 60})
    chain["nodes"][1]["timeout"] = "2h"

    with pytest.raises(
        PolicyError, match=r"gate review's timeout 2h > its total_time_cap_minutes 60"
    ):
        _materialize(chain)


# ── per-level defaults and maxima (Ruling 211) ────────────────────────────────

#: Omid's shape (Ruling 211), for any one cap field.
LEVELS = {"work_item": 480, "nodes": 180, "steps": 120, "tasks": 90}


def _levels(field: str, values: dict = LEVELS) -> dict:
    return {level: {field: v} for level, v in values.items()}


@pytest.mark.parametrize("field", _policy.SCOPE_CAP_FIELDS)
@pytest.mark.parametrize("section", ["defaults", "maxima"])
def test_a_flat_cap_is_refused_at_load_naming_its_level(tmp_path, section, field):
    """The flat form was rc-only and never shipped in a release (Ruling 211):
    refused, pointing at the per-level form."""
    path = tmp_path / "policy.yaml"
    path.write_text(
        yaml.safe_dump({"default": {"attempts": 3, "wall_clock_s": 60}, section: {field: 30}})
    )

    with pytest.raises(PolicyError, match=f"use {section}.tasks.{field}"):
        _policy.PolicyInput.from_yaml(path)


@pytest.mark.parametrize("section", ["defaults", "maxima"])
@pytest.mark.parametrize(
    ("values", "names"),
    [
        ({"steps": 120, "tasks": 150}, "{s}.tasks.{f} 150 exceeds {s}.steps.{f} 120"),
        ({"work_item": 100, "tasks": 150}, "{s}.tasks.{f} 150 exceeds {s}.work_item.{f} 100"),
        ({"work_item": 100, "nodes": 180}, "{s}.nodes.{f} 180 exceeds {s}.work_item.{f} 100"),
    ],
    ids=["task-over-step", "task-over-work-item-past-unset-levels", "node-over-work-item"],
)
def test_a_narrower_levels_cap_above_a_broader_ones_is_refused_naming_both(section, values, names):
    field = "time_cap_minutes"
    with pytest.raises(ValueError, match=names.format(s=section, f=field)):
        InstancePolicyInput.model_validate({section: _levels(field, values)})


def test_a_levels_default_above_its_maximum_is_refused_naming_both():
    """A default is held to its own level's maximum, or the nearest broader
    one, which bounds every level under it."""
    with pytest.raises(ValueError, match="defaults.steps.budget_usd 30 exceeds maxima.nodes"):
        InstancePolicyInput.model_validate(
            {"defaults": {"steps": {"budget_usd": 30}}, "maxima": {"nodes": {"budget_usd": 20}}}
        )


@pytest.mark.parametrize("field", _policy.SCOPE_CAP_FIELDS)
def test_each_scope_runs_under_its_levels_default_where_nothing_set_one(field):
    chain = _materialize(_nodes(), defaults=_levels(field))
    node, gate = chain.chain.nodes
    by_path = {t.path: t for t in node.tasks()}

    assert getattr(chain.work_item_policy(), field) == 480
    assert getattr(chain.policy_for(node), field) == 180
    assert getattr(chain.policy_for(gate), field) == 180
    assert getattr(chain.policy_for(node.steps[0]), field) == 120
    assert getattr(chain.policy_for(by_path["build.run.impl"]), field) == 90
    # A recovery task is a task too.
    assert getattr(chain.policy_for(by_path["build.run.impl.on_failure.main.fix"]), field) == 90


@pytest.mark.parametrize("field", _policy.SCOPE_CAP_FIELDS)
def test_a_value_the_chain_or_the_item_sets_replaces_every_levels_default_under_it(field):
    """Ruling 211: a level's default applies unless the chain, node, step,
    task or the item's own override sets a value. An inherited value is held
    to each level's maximum."""
    chain = _materialize(
        _nodes(chain={field: 600}),
        defaults=_levels(field),
        maxima={"work_item": {field: 1440}, "tasks": {field: 240}},
    )
    node = chain.chain.nodes[0]
    impl = next(t for t in node.tasks() if t.path == "build.run.impl")

    assert getattr(chain.work_item_policy(), field) == 600
    assert getattr(chain.policy_for(node), field) == 600
    assert getattr(chain.policy_for(impl), field) == 240

    raised = chain.with_item_policy({field: 1000})

    assert getattr(raised.work_item_policy(), field) == 1000
    assert getattr(raised.policy_for(node), field) == 1000
    assert getattr(raised.policy_for(impl), field) == 240


@pytest.mark.parametrize("field", _policy.SCOPE_CAP_FIELDS)
def test_a_scope_may_exceed_its_levels_default_but_not_its_levels_maximum(field):
    maxima = {"work_item": {field: 1440}, "tasks": {field: 240}}
    chain = _materialize(_nodes(task={field: 200}), defaults=_levels(field), maxima=maxima)
    impl = next(t for t in chain.chain.nodes[0].tasks() if t.path == "build.run.impl")
    assert getattr(chain.policy_for(impl), field) == 200

    with pytest.raises(PolicyError) as refused:
        _materialize(_nodes(task={field: 300}), defaults=_levels(field), maxima=maxima)

    assert (
        f"task build.run.impl sets {field} 300 > the administrator maximum 240 "
        f"(maxima.tasks.{field}" in str(refused.value)
    )
    assert refused.value.field == field


@pytest.mark.parametrize("field", _policy.SCOPE_CAP_FIELDS)
def test_an_items_own_cap_is_bounded_by_the_work_item_maximum_its_paths_by_their_levels(field):
    """The item-wide value is the work item's own (Ruling 211), up to
    `maxima.work_item`; one on a path may exceed its level's default, up to
    its level's maximum."""
    chain = _materialize(
        _nodes(),
        defaults=_levels(field),
        maxima={"work_item": {field: 1440}, "tasks": {field: 240}},
    )
    impl = "build.run.impl"

    assert getattr(chain.with_item_policy({field: 1400}).work_item_policy(), field) == 1400
    on_path = chain.with_item_policy({"paths": {impl: {field: 200}}})
    assert getattr(on_path.policy_at(impl), field) == 200

    with pytest.raises(PolicyError) as wide:
        chain.with_item_policy({field: 1500})
    with pytest.raises(PolicyError) as path:
        chain.with_item_policy({"paths": {impl: {field: 300}}})

    assert wide.value.field == f"policy.{field}"
    assert path.value.field == f"policy.paths.{impl}.{field}"
    assert f"maxima.tasks.{field}" in str(path.value)


def test_a_snapshot_frozen_before_ruling_211_reads_its_flat_maxima_as_the_work_items():
    """An rc snapshot's flat maximum bounded every scope; the `work_item`
    maximum does now, so an item filed then runs under the same bounds."""
    chain = _materialize(_nodes())
    stored = json.loads(chain.to_json())
    stored["policy"]["maxima"].update(
        {"time_cap_minutes": 60, "token_budget": None, "work_item": {}}
    )
    del stored["policy"]["cap_defaults"]

    read = MaterializedChain.from_json(json.dumps(stored))

    impl = next(t for t in read.chain.nodes[0].tasks() if t.path == "build.run.impl")
    assert read.policy.maxima.work_item.time_cap_minutes == 60
    assert read.policy_for(impl).time_cap_minutes == 60


# ── a work item's own cap only tightens ───────────────────────────────────────


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize(
    ("override", "where"),
    [
        ({"paths": {"build.run": {"{f}": 20}}}, "policy.paths.build.run.{f}"),
        ({"paths": {"build": {"{f}": 5}, "build.run": {"{f}": 8}}}, "policy.paths.build.run.{f}"),
    ],
    ids=["path-over-its-own", "path-over-an-enclosing-override"],
)
def test_an_items_path_cap_above_the_one_it_lands_on_is_refused(field, override, where):
    """An item's override on a path only tightens that inner scope: inner
    scopes keep their authored caps (Ruling 198). Only its item-wide cap --
    the work item's own -- may be raised."""
    chain = _materialize(_nodes(chain={field: 60}, step={field: 10}))
    raw = json.loads(json.dumps(override).replace("{f}", field))

    with pytest.raises(PolicyError) as refused:
        chain.with_item_policy(raw)

    assert refused.value.field == where.format(f=field)
    assert "only tightens a cap" in str(refused.value)


@pytest.mark.parametrize("field", FIELDS)
def test_an_item_may_raise_its_own_cap_above_the_chains_up_to_the_maximum(field):
    """Ruling 198: the item is the outer scope, so its own cap may exceed the
    chain's, up to `maxima:`. A scope that set its own keeps it; one that set
    none takes the item's; and above the maximum it is refused."""
    chain = _materialize(
        _nodes(chain={field: 60}, step={field: 10}), maxima={"work_item": {field: 600}}
    )
    raised = chain.with_item_policy({field: 120})
    by_path = {t.path: t for n in raised.chain.nodes for t in n.tasks()}

    assert getattr(raised.policy_for(raised.chain.nodes[0]), field) == 120
    assert getattr(raised.policy_for(raised.chain.nodes[0].steps[0]), field) == 10
    assert getattr(raised.policy_for(by_path["build.run.impl"]), field) == 10
    with pytest.raises(PolicyError) as refused:
        chain.with_item_policy({field: 601})
    assert refused.value.field == f"policy.{field}"
    assert "administrator maximum 600" in str(refused.value)


@pytest.mark.parametrize("field", FIELDS)
def test_a_retry_raising_a_cap_above_its_parents_is_refused_naming_both(field):
    """Kraft-vs3fd: the retry door refuses through the same check as intake,
    so the refusal names both scopes."""
    from kraft.templates.retry import RetryOverrideError, validate_retry_override

    chain = _materialize(_nodes(step={field: 10}))

    with pytest.raises(RetryOverrideError) as refused:
        validate_retry_override(chain, "build.run.impl", policy={field: 20})

    assert refused.value.field == f"policy.{field}"
    assert f"task build.run.impl sets {field} 20 > its step build.run's 10" in str(refused.value)


@pytest.mark.parametrize("field", FIELDS)
def test_an_items_cap_tightens_every_scope_under_it_that_set_a_looser_one(field):
    chain = _materialize(_nodes(chain={field: 60}, step={field: 10})).with_item_policy(
        {field: 30, "paths": {"build.run.impl": {field: 4}}}
    )
    by_path = {t.path: t for n in chain.chain.nodes for t in n.tasks()}

    assert getattr(chain.policy_for(chain.chain.nodes[0]), field) == 30
    assert getattr(chain.policy_for(chain.chain.nodes[0].steps[0]), field) == 10
    assert getattr(chain.policy_for(by_path["build.run.impl"]), field) == 4


# ── wait_timeout_minutes is retired (Ruling 196) ─────────────────────────────


def _wait_chain(**task) -> dict:
    wait = {"id": "ci", "kind": "forge", "target": "mr.ci", **task}
    return {"id": "c", "nodes": [{"id": "feedback", "kind": "exec", "tasks": [wait]}]}


@pytest.fixture
def warnings_afresh(monkeypatch):
    """A deprecation is said once per process; these tests hear it anew."""
    monkeypatch.setattr(_policy, "_DEPRECATIONS_SAID", set())


def test_a_wait_timeout_written_before_ruling_196_reads_as_the_tasks_total_cap(
    caplog, warnings_afresh
):
    with caplog.at_level(logging.WARNING):
        chain = _materialize(
            _wait_chain(wait={"timeout": "90s", "polling": {"max_interval": "5m"}})
        )

    (ci,) = chain.chain.nodes[0].tasks()
    assert ci.task.policy.total_time_cap_minutes == 2  # rounded up to a whole minute
    assert ci.task.wait_bounds(chain.policy_for(ci)).timeout.total_seconds() == 120
    assert "deprecated" in caplog.text


def test_a_policy_file_written_before_ruling_196_reads_its_wait_maximum_as_the_total_cap(
    tmp_path, caplog, warnings_afresh
):
    path = tmp_path / "policy.yaml"
    path.write_text(
        "default: {attempts: 3, wall_clock_s: 3600}\nmaxima: {wait_timeout_minutes: 600}\n"
    )

    with caplog.at_level(logging.WARNING):
        parsed = _policy.PolicyInput.from_yaml(path)

    assert parsed.maxima.tasks.total_time_cap_minutes == 600
    assert "deprecated" in caplog.text


def test_a_snapshot_frozen_before_ruling_196_still_reads():
    """Every snapshot dumped the old fields, most of them `null`. A resolved
    `wait_timeout_minutes` meant "every wait", which no scope's total cap
    does, so it is dropped: each wait keeps its own task's cap."""
    chain = _materialize(_wait_chain(policy={"total_time_cap_minutes": 45}))
    stored = json.loads(chain.to_json())
    stored["policy"]["wait_timeout_minutes"] = 30
    stored["policy"]["maxima"]["wait_timeout_minutes"] = None
    del stored["chain"]["nodes"][0]["tasks"][0]["policy"]
    stored["chain"]["nodes"][0]["tasks"][0]["wait"] = {"timeout": "45m", "polling": {}}

    read = MaterializedChain.from_json(json.dumps(stored))

    (ci,) = read.chain.nodes[0].tasks()
    assert read.policy.total_time_cap_minutes is None
    assert ci.task.wait_bounds(read.policy_for(ci)).timeout.total_seconds() == 45 * 60


@pytest.mark.parametrize(
    "override",
    [{"wait_timeout_minutes": 30}, {"paths": {"feedback.main.ci": {"wait_timeout_minutes": 30}}}],
    ids=["item-wide", "on-a-path"],
)
def test_a_write_naming_wait_timeout_minutes_is_refused_naming_its_replacement(override):
    chain = _materialize(_wait_chain(policy={"total_time_cap_minutes": 45}))

    with pytest.raises(PolicyError) as refused:
        chain.with_item_policy(override)

    assert refused.value.field.endswith("wait_timeout_minutes")
    assert "total_time_cap_minutes" in str(refused.value)
    # And by the model itself, for any writer that skips the chain's check.
    with pytest.raises(ValueError, match="retired"):
        _policy.WorkItemPolicy.model_validate(override)


def test_a_stored_override_reads_its_retired_wait_timeouts_as_the_caps_that_replaced_them():
    """Ruling 196's migration: a path's value becomes that path's total cap;
    the item-wide one -- every wait, in its old meaning -- becomes each wait
    task's, never a cap on the whole item."""
    from kraft import store

    nodes = _wait_chain(policy={"total_time_cap_minutes": 45})
    nodes["nodes"].append({"id": "build", "kind": "exec", "tasks": [_sub("impl")]})
    chain = _materialize(nodes)
    row = {
        "id": "w",
        "materialized_chain": chain.to_json(),
        "policy_override": json.dumps(
            {"wait_timeout_minutes": 20, "paths": {"build.run.impl": {"wait_timeout_minutes": 7}}}
        ),
    }

    class Row(dict):
        def keys(self):
            return list(super().keys())

    item = store.policy_override_of(Row(row))

    # Read without the chain to spread it over, an item-wide one is dropped.
    bare = _policy.WorkItemPolicy.model_validate(
        {"wait_timeout_minutes": 20}, context=_policy.FROZEN
    )
    assert bare.total_time_cap_minutes is None and bare.paths == {}
    assert item.total_time_cap_minutes is None
    assert item.paths["feedback.main.ci"].total_time_cap_minutes == 20
    assert item.paths["build.run.impl"].total_time_cap_minutes == 7


# ── a spend cap is the same mechanism (Ruling 195) ────────────────────────────


@pytest.mark.parametrize(
    ("field", "big", "small"), [("token_budget", 20, 10), ("budget_usd", 2.5, 1)]
)
def test_a_child_budget_above_its_parents_is_refused_naming_both(field, big, small):
    with pytest.raises(PolicyError) as refused:
        _materialize(_nodes(step={field: small}, task={field: big}))

    assert f"task build.run.impl sets {field} {big} > its step build.run's {small}" in str(
        refused.value
    )


@pytest.mark.parametrize(
    ("field", "big", "small"), [("token_budget", 20, 10), ("budget_usd", 2.5, 1)]
)
def test_an_item_may_raise_its_own_budget_above_the_chains_up_to_the_maximum(field, big, small):
    """Ruling 198: the item is the outer scope; its own budget may exceed the
    chain's, up to `maxima`, and is refused above it."""
    chain = _materialize(_nodes(chain={field: small}), maxima={"work_item": {field: big * 2}})

    raised = chain.with_item_policy({field: big})

    assert getattr(raised.policy_for(raised.chain.nodes[0]), field) == big
    with pytest.raises(PolicyError) as refused:
        chain.with_item_policy({field: big * 3})
    assert refused.value.field == f"policy.{field}"


@pytest.mark.parametrize(("field", "value"), [("token_budget", 900), ("budget_usd", 9)])
def test_a_budget_may_be_raised_above_the_default_up_to_the_maximum(field, value):
    """Ruling 198: a `defaults:` budget is a default, not a ceiling."""
    chain = _materialize(
        _nodes(node={field: value}),
        defaults=_every_level(**{field: 100 if field == "token_budget" else 1}),
        maxima={"work_item": {field: value}},
    )

    assert getattr(chain.policy_for(chain.chain.nodes[0]), field) == value
    with pytest.raises(PolicyError, match="administrator maximum"):
        _materialize(_nodes(node={field: value * 2}), maxima={"nodes": {field: value}})


# ── the shipped default (Kraft-nxqft) ─────────────────────────────────────────


@pytest.mark.parametrize("chain_id", ["default", "quick-task"])
def test_the_shipped_implementer_runs_under_a_default_time_cap(chain_id):
    """A worker that runs away is stopped for a person rather than left to
    spend: the incident ran 68 minutes and $9.46 with nothing bounding it.
    The value clears every successful implementer run on record (the longest
    96 minutes), so it binds only a run that has already gone wrong."""
    seeded = Path(__file__).resolve().parents[2] / "templates"
    chain = (
        TemplateLibrary.from_yaml_dir(seeded)
        .resolve_chain(chain_id)
        .materialize(
            target=WorkItemTarget.for_repository("target"),
            effective_policy=InstancePolicy.from_input(InstancePolicyInput.model_validate({})),
        )
    )

    assert chain.policy_at("implementation.main.implement").time_cap_minutes == 120
