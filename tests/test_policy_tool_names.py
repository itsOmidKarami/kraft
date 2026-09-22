"""A policy tool list names tools, never permission rules (Kraft-9i6xy).

The permission gate (`sessions.permission_request`) matches a tool name
exactly, so a scoped rule (`Bash(git *)`) or a glob (`mcp__x__*`) in
`allowed_tools` or `deny_tools` could never match an ask: it would bound
nothing it claims to. Every door policy loads through refuses one, naming
the field and the name to write instead.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from kraft import config
from kraft.policy import (
    InstancePolicy,
    InstancePolicyInput,
    PolicyError,
    PolicyInput,
    TaskPolicyOverride,
    TemplatePolicyOverride,
)
from kraft.templates.environment import WorkItemTarget
from kraft.templates.models import Chain, ResolvedChain
from kraft.templates.retry import RetryOverrideError, validate_retry_override


@pytest.mark.parametrize(
    ("name", "suggests"),
    [
        ("Bash", None),
        ("NotebookEdit", None),
        ("mcp__kraft__report_progress", None),
        ("mcp__claude_ai_Google_Drive__search_files", None),
        ("mcp__my-server__do.it", None),
        ("Bash(git *)", "'Bash'"),
        ("Bash(git status)", "'Bash'"),
        ("mcp__kraft__*", "exact MCP tool name"),
        ("mcp__kraft", "exact MCP tool name"),
        ("Bash*", "exact tool name"),
        ("Read Edit", "exact tool name"),
    ],
    ids=[
        "built-in",
        "built-in-camel-case",
        "mcp-tool",
        "mcp-tool-underscored-server",
        "mcp-tool-hyphen-and-dot",
        "scoped-rule",
        "scoped-rule-exact-command",
        "mcp-glob",
        "mcp-server-rule",
        "glob",
        "two-names-in-one",
    ],
)
@pytest.mark.parametrize("field", ["allowed_tools", "deny_tools"])
def test_a_tool_list_holds_only_names_the_gate_can_match(field, name, suggests):
    if suggests is None:
        assert getattr(TaskPolicyOverride.model_validate({field: [name]}), field) == [name]
        return
    with pytest.raises(ValidationError, match=rf"{field}: {re.escape(repr(name))} .*{suggests}"):
        TaskPolicyOverride.model_validate({field: [name]})


def _instance(tmp_path, field, name):
    path = tmp_path / "policy.yaml"
    path.write_text(
        yaml.safe_dump({"default": {"attempts": 1, "wall_clock_s": 1}, "maxima": {field: [name]}})
    )
    PolicyInput.from_yaml(path)


def _repository(tmp_path, field, name, *, in_policy=True):
    entry = {
        "path": str(tmp_path),
        **({"policy": {field: [name]}} if in_policy else {field: [name]}),
    }
    path = tmp_path / "repos.yaml"
    path.write_text(yaml.safe_dump({"repos": [entry]}))
    config.load_repos(path)


def _template(tmp_path, field, name):
    task = {"id": "t", "kind": "agent", "harness": "h", "prompt": "p", "policy": {field: [name]}}
    Chain.model_validate({"id": "c", "nodes": [{"id": "n", "kind": "exec", "tasks": [task]}]})


def _retry(tmp_path, field, name):
    task = {"id": "t", "kind": "agent", "harness": "h", "prompt": "p"}
    chain = ResolvedChain.from_chain(
        Chain.model_validate({"id": "c", "nodes": [{"id": "n", "kind": "exec", "tasks": [task]}]})
    ).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput()),
    )
    validate_retry_override(chain, "n", policy={field: [name]})


def _work_item(tmp_path, field, name):
    # The work-item layer (Kraft-ab1bh) is a `TemplatePolicyOverride`, the
    # same model the repository and node layers are.
    TemplatePolicyOverride.model_validate({field: [name]})


@pytest.mark.parametrize(
    ("load", "field", "refusal"),
    [
        (_instance, "allowed_tools", PolicyError),
        (_repository, "allowed_tools", config.ConfigError),
        (_repository, "deny_tools", config.ConfigError),
        (
            lambda *a: _repository(*a, in_policy=False),
            "deny_tools",
            config.ConfigError,
        ),
        (_template, "allowed_tools", ValidationError),
        (_template, "deny_tools", ValidationError),
        (_retry, "allowed_tools", RetryOverrideError),
        (_retry, "deny_tools", RetryOverrideError),
        (_work_item, "allowed_tools", ValidationError),
        (_work_item, "deny_tools", ValidationError),
    ],
    ids=[
        "instance-maxima",
        "repository-policy-allowed",
        "repository-policy-denied",
        "repository-entry-denied",
        "template-allowed",
        "template-denied",
        "retry-override-allowed",
        "retry-override-denied",
        "work-item-allowed",
        "work-item-denied",
    ],
)
def test_every_policy_door_refuses_a_rule_naming_the_field(tmp_path, load, field, refusal):
    with pytest.raises(refusal, match=rf"{field}: 'Bash\(git \*\)' .*'Bash'"):
        load(tmp_path, field, "Bash(git *)")


def with_a_frozen_rule(raw: str, field: str = "allowed_tools") -> str:
    """`raw`, a stored snapshot, as a build before Kraft-9i6xy could have
    frozen it: its first node's policy lists a scoped rule."""
    import json

    doc = json.loads(raw)
    doc["chain"]["nodes"][0]["policy"] = {field: ["Bash(git *)"]}
    return json.dumps(doc)


def test_a_snapshot_frozen_with_a_rule_still_renders_on_the_board(client, repo):
    """Kraft-9ct4q: rule syntax is refused where a policy is written, never
    where a snapshot is read -- an item frozen before the refusal existed
    must not 500 its board page on every read. Its launch stops instead
    (tests/executor/test_policy_enforcement.py)."""
    import sqlite3

    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "t", "chain_template": "quick-task", "autostart": False},
    ).json()["id"]
    db = Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db"
    conn = sqlite3.connect(db)
    try:
        with conn:
            (raw,) = conn.execute(
                "SELECT materialized_chain FROM work_items WHERE id = ?", (wid,)
            ).fetchone()
            conn.execute(
                "UPDATE work_items SET materialized_chain = ? WHERE id = ?",
                (with_a_frozen_rule(raw), wid),
            )
    finally:
        conn.close()

    r = client.get(f"/api/work-items/{wid}")

    assert r.status_code == 200, r.text
    assert r.json()["id"] == wid


def test_a_fork_record_frozen_with_a_rule_still_reads():
    """Kraft-9ct4q, for a retry override stored on a run fork before the
    refusal: read back as it was frozen, and refused at launch like a
    snapshot's rule."""
    from kraft.templates.forks import override_from_record

    task = {"id": "t", "kind": "agent", "harness": "h", "prompt": "p"}
    chain = ResolvedChain.from_chain(
        Chain.model_validate({"id": "c", "nodes": [{"id": "n", "kind": "exec", "tasks": [task]}]})
    ).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput()),
    )
    record = {
        "path": "n",
        "task_config": {},
        "policy": {"allowed_tools": ["Bash(git *)"]},
        "chain": with_a_frozen_rule(chain.to_json()),
    }

    read = override_from_record(record)

    assert read.policy.allowed_tools == ["Bash(git *)"]
