"""A stop carries what the chain or a repair concluded a person should do next
(Kraft-s7c04.27): a repair that decided no repair can help says so in its
result file, and the stop records it as `suggested_action` rather than only
in prose."""

import json
import shlex
from types import SimpleNamespace

import pytest

from kraft import executor
from kraft.executor import prompts, stops

NO_SETUP = {"setup_command": ""}


def _walk(it):
    return executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
    )


def _failing_repair(result: object) -> dict:
    """A node repair that writes `result` to its result file and fails."""
    script = f'printf %s {shlex.quote(json.dumps(result))} > "$KRAFT_RESULT_PATH"; exit 1'
    command = shlex.join(["sh", "-c", script])
    return {"tasks": [{"id": "fix", "kind": "subprocess", "command": command}]}


def _node(repair: dict) -> dict:
    return {
        "id": "build",
        "kind": "exec",
        "steps": [{"id": "check", "tasks": [{"id": "a", "kind": "subprocess", "command": "true"}]}],
        "on_failure": repair,
    }


SKIP = {"action": "skip", "reason": "the branch has no MR; a retry would re-push it"}


@pytest.mark.parametrize(
    ("written", "recorded"),
    [
        ({"status": "failed", "suggested_action": SKIP}, SKIP),
        (
            {"status": "failed", "suggested_action": {"action": "abandon"}},
            {"action": "abandon", "reason": ""},
        ),
        ({"status": "failed", "suggested_action": {"action": "merge", "reason": "x"}}, None),
        ({"status": "failed", "suggested_action": "skip"}, None),
        ({"status": "failed"}, None),
    ],
    ids=["skip", "no-reason", "unknown-action", "not-an-object", "absent"],
)
async def test_a_failed_repair_s_suggestion_rides_the_stop(item_on, script, written, recorded):
    script.plan = {"a": ["failed"]}
    script.real = {"fix"}
    it = await item_on([_node(_failing_repair(written))])

    assert await _walk(it) == "needs_human"
    [stop] = it.events("work_item_needs_human")
    assert stop["payload"].get("suggested_action") == recorded


async def test_every_recovery_is_told_how_to_suggest_an_action(item_on, script):
    script.plan = {"a": ["failed", "done"]}
    it = await item_on([_node({"tasks": [{"id": "fix", "kind": "subprocess", "command": "true"}]})])

    await _walk(it)
    [steer] = script.steers["fix"]
    assert prompts.SUGGEST_ACTION in steer.take()


async def test_an_infra_stop_suggests_a_retry(item_on, script, database):
    it = await item_on([_node(_failing_repair({}))])

    assert await stops.stop_for_infra(database, it.id, SimpleNamespace(id="build")) == "needs_human"
    [stop] = it.events("work_item_needs_human")
    assert stop["payload"]["suggested_action"] == stops.RETRY_LATER
