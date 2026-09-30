"""A `kind: kit` item: fetched by the walk before anything reads its sandbox,
fetched again at dispatch, lowered for every launch, and stopped naming the
Kit when it cannot be used (spec §9.5)."""

import json
from pathlib import Path

import pytest
import yaml
from support.harness import entry_of, v1_chain, v1_resolved, v1_walk

from kraft import builtins as kraft_builtins
from kraft import executor
from kraft.executor import dispatch
from kraft.paths import default_templates_dir
from kraft.policy import TemplatePolicyOverride
from kraft.templates.models import ResolvedChain
from kraft.worker import kit

FIXTURES = Path(__file__).parents[1] / "fixtures" / "kit" / "m7"
DESCRIPTOR = (FIXTURES / "kraft" / "egress-credential-resources.yaml").read_text()
EXPECTED = yaml.safe_load((FIXTURES / "expected.yaml").read_text())["kraft"][
    "egress-credential-resources"
]
REF = EXPECTED["policy"]["image"]
KIT = {"kind": "kit", "runtime": "docker", "kit": REF}
#: What every launch of the item is handed: the lowered policy, plus the Kit.
LOWERED = {**EXPECTED["policy"], "kit": REF}
LIFECYCLE = json.dumps(
    {
        "schemaVersion": "3",
        "kind": "workload",
        "capabilities": [
            {"type": kit.NETWORK, "config": {"runtime": {"allow": ["x.io"]}}},
            {"type": "com.docker.sandbox/lifecycle@1", "config": {}},
        ],
    }
)
_TASK = [
    {"id": "work", "kind": "exec", "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}]}
]


@pytest.fixture
def fetched(monkeypatch) -> list[str]:
    """`kit.fetch` answering the claude worker Kit, recorded; `sandbox.yaml`
    binding its credential."""
    templates = default_templates_dir()
    templates.mkdir(parents=True, exist_ok=True)
    (templates / "sandbox.yaml").write_text(yaml.safe_dump({"credentials": EXPECTED["bindings"]}))
    calls: list[str] = []

    async def fetch(ref):
        calls.append(ref)
        return kit.Fetched(ref, ref.rpartition("@")[2], DESCRIPTOR)

    monkeypatch.setattr(kit, "fetch", fetch)
    return calls


def _launched(monkeypatch) -> list:
    """The sandbox each setup command and task launch is handed."""
    seen = []

    async def run_setup_command(worktree, repo, repo_entry, *, sandbox=None, checkout=None):
        seen.append(("setup", sandbox))
        return ""

    async def run_task(*_a, sandbox=None, **_kw):
        seen.append(("task", sandbox))
        return "done"

    monkeypatch.setattr(kraft_builtins, "run_setup_command", run_setup_command)
    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)
    return seen


async def test_a_fresh_kit_item_gets_past_the_walk(tmp_path, repo, monkeypatch, fetched):
    """Nothing cached: the walk fetches the Kit before it reads the sandbox,
    so `ensure_worktree`, `prepare_runtime` and the task all run in the
    lowered policy, and dispatch finds it cached."""
    seen = _launched(monkeypatch)

    status, evts, *_ = await v1_walk(
        tmp_path,
        v1_chain(_TASK, repo=repo),
        repo=repo,
        repo_entry=entry_of({"setup_command": "true", "sandbox": KIT}),
    )

    assert status == "completed", [e["payload"] for e in evts if "needs_human" in e["type"]]
    assert fetched == [REF]
    assert seen == [("setup", LOWERED), ("setup", LOWERED), ("task", LOWERED)]


async def _cannot_fetch(ref):
    raise kit.KitRefused(f"`manifest inspect {ref}` failed: denied: requested access")


@pytest.mark.parametrize(
    ("fetch", "reason"),
    [
        (_cannot_fetch, "denied: requested access"),
        (None, "requires com.docker.sandbox/lifecycle@1"),
    ],
    ids=["fetch-failed", "refused-type"],
)
@pytest.mark.parametrize("where", ["walk-needs-human", "dispatch-config-error"])
async def test_a_kit_that_cannot_be_used_stops_the_item(
    tmp_path, repo, item_on, monkeypatch, fetched, fetch, reason, where
):
    """The stop names the Kit and why, and nothing launches: never an
    unsandboxed fallback."""
    if fetch is not None:
        monkeypatch.setattr(kit, "fetch", fetch)
    else:
        monkeypatch.setattr(kit, "decode", lambda _t, real=kit.decode: real(LIFECYCLE))
    seen = _launched(monkeypatch)
    entry = entry_of({"setup_command": "", "sandbox": KIT})

    if where == "walk-needs-human":
        status, evts, *_ = await v1_walk(
            tmp_path, v1_chain(_TASK, repo=repo), repo=repo, repo_entry=entry
        )
        stop = next(e["payload"] for e in evts if e["type"] == "work_item_needs_human")
        assert status == "needs_human"
        told = json.dumps(stop)
    else:
        it = await item_on(_TASK, "work")
        node = it.chain.chain.nodes[0]
        status = await dispatch.dispatch_node(
            it.database,
            it.run_dirs,
            node.steps[0].tasks[0],
            node,
            it.row(),
            it.repo,
            launch=executor.LaunchContext(repo_entry=entry),
        )
        [session] = it.sessions()
        assert (status, session["status"]) == ("config_error", "config_error")
        told = Path(session["log_path"]).read_text()
    assert REF in told
    assert reason in told
    assert seen == []


async def test_a_caller_outside_the_walk_and_dispatch_with_no_cached_kit_stops(item_on):
    """Review reply, gate review, escalation and the rest read the cache
    alone: a miss is `SandboxUnresolved`, which each already stops on."""
    it = await item_on(_TASK)
    launch = executor.LaunchContext(repo_entry=entry_of({"setup_command": "", "sandbox": KIT}))

    with pytest.raises(dispatch.SandboxUnresolved, match=f"the Kit {REF} has not been fetched"):
        dispatch.item_sandbox(it.row(), launch)


async def test_a_kit_frozen_in_a_chain_layer_lowers_like_a_repositorys(item_on, fetched):
    await kit.ensure(REF)
    resolved = v1_resolved(_TASK)
    chain = ResolvedChain.from_chain(
        resolved.chain.model_copy(update={"policy": TemplatePolicyOverride(sandbox=KIT)})
    )
    frozen = await item_on(chain)
    live = await item_on(_TASK, wid="w2")
    entry = executor.LaunchContext(repo_entry=entry_of({"setup_command": "", "sandbox": KIT}))

    assert dispatch.item_sandbox(frozen.row(), None) == LOWERED
    assert dispatch.item_sandbox(live.row(), entry) == LOWERED


async def test_the_resolution_is_recorded_once_per_item_and_digest(item_on, fetched):
    """At dispatch: what the Kit was read from, and what of it was skipped or
    ignored. A second dispatch of the same Kit records nothing new."""
    it = await item_on(_TASK)
    other = REF.replace("a" * 64, "b" * 64)
    for ref in (REF, REF, other):
        await dispatch.record_kit(it.database, it.id, *await kit.resolve(ref))

    assert [e["payload"] for e in it.events("sandbox_kit_resolved")] == [
        {"kit": ref, "manifest": ref.rpartition("@")[2], "skipped": [], "ignored": []}
        for ref in (REF, other)
    ]
