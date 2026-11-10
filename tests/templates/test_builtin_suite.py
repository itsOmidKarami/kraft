"""The `suite` option of `kraft.verify_changed_test_scopes`: which of a
scope's commands the task runs, and where it may be written."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from support.harness import v1_chain

from kraft.addons import load
from kraft.templates import models as tm
from kraft.templates.library import TemplateLibrary

_VERIFY = "kraft.verify_changed_test_scopes"
_CONFIG = Path(__file__).resolve().parents[2] / "config"

#: The chain the "Run the full suite as its own node" guide shows.
FULL_SUITE_CHAIN = {
    "id": "full-suite",
    "extends": "default",
    "add": [
        {
            "after": "verification",
            "node": {
                "id": "full_suite",
                "kind": "exec",
                "tasks": [{"id": "tests", "extends": "verify_changed_scopes", "suite": "full"}],
                "fix_loop": {
                    "tasks": [{"id": "repair", "extends": "repair_verification"}],
                    "judge": {"id": "judge", "extends": "strict_judge"},
                    "max_attempts": 2,
                },
            },
        }
    ],
}


@pytest.mark.parametrize(
    ("fields", "suite", "refused"),
    [
        pytest.param({"ref": _VERIFY}, tm.TestSuite.TARGETED, None, id="default"),
        pytest.param({"ref": _VERIFY, "suite": "full"}, tm.TestSuite.FULL, None, id="full"),
        pytest.param({"ref": _VERIFY, "suite": "everything"}, None, "suite", id="unknown-value"),
        pytest.param(
            {"ref": "kraft.mr_rebase", "suite": "full"},
            None,
            "only kraft.verify_changed_test_scopes",
            id="full-on-rebase",
        ),
        pytest.param(
            {"ref": "kraft.mr_rebase", "suite": "targeted"},
            tm.TestSuite.TARGETED,
            None,
            id="default-spelled-out-on-rebase",
        ),
    ],
)
def test_the_suite_option_is_valid_only_on_the_verify_task(fields, suite, refused):
    raw = {"id": "t", "kind": "builtin", **fields}
    if refused:
        with pytest.raises(ValidationError, match=refused):
            tm.BuiltinTask.model_validate(raw)
    else:
        assert tm.BuiltinTask.model_validate(raw).suite is suite


def test_a_stored_chain_loads_again(tmp_path):
    chain = v1_chain(
        [
            {
                "id": "m",
                "kind": "exec",
                "tasks": [{"id": "r", "kind": "builtin", "ref": "kraft.mr_rebase"}],
            }
        ],
        repo=tmp_path,
    )

    raw = chain.to_json()

    assert '"suite":"targeted"' in raw  # the default really is dumped
    tm.MaterializedChain.from_json(raw)


def test_the_no_ci_chain_of_the_guide_resolves(tmp_path):
    shutil.copytree(_CONFIG, tmp_path / "config")
    root = tmp_path / "config"
    (root / "chains" / "full-suite.yaml").write_text(yaml.safe_dump(FULL_SUITE_CHAIN))

    resolved = TemplateLibrary.from_yaml_dir(root).resolve_chain("full-suite")

    ids = [n.id for n in resolved.nodes]
    assert ids[ids.index("verification") + 1] == "full_suite"
    suite = {
        n.id: [t.task.suite for t in n.tasks() if t.task.kind is tm.TaskKind.BUILTIN]
        for n in resolved.nodes
        if n.id in ("verification", "full_suite")
    }
    assert suite == {
        "verification": [tm.TestSuite.TARGETED],
        "full_suite": [tm.TestSuite.FULL],
    }


@pytest.mark.parametrize("opts_in", [False, True], ids=["shipped-default", "opted-in"])
def test_a_chain_opts_into_the_rebase_conflict_resolver_by_naming_it(
    tmp_path, monkeypatch, opts_in
):
    """`resolve_rebase_conflict` is on no shipped chain: only a chain that names
    it, prefixed `kraft-default:` as a seeded home's empty library needs,
    under `on_base_changed.on_conflict` runs it."""
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    root = tmp_path / "config"
    (root / "chains").mkdir(parents=True)
    (root / "library.yaml").write_text("tasks: {}\n")
    chain = {"id": "default", "extends": "kraft-default:default"}
    if opts_in:
        handler = {"tasks": [{"id": "resolve", "extends": "kraft-default:resolve_rebase_conflict"}]}
        chain["overrides"] = {
            "merge_request_feedback": {"on_base_changed": {"on_conflict": handler}}
        }
    (root / "chains" / "default.yaml").write_text(yaml.safe_dump(chain))

    library = TemplateLibrary.from_yaml_dir(root, addons=load.installed(root))
    feedback = next(
        n for n in library.resolve_chain("default").nodes if n.id == "merge_request_feedback"
    )
    handler = feedback.node.on_base_changed.on_conflict

    assert feedback.node.on_base_changed.restart_from == "verification"
    if not opts_in:
        assert handler is None
        assert "resolve_rebase_conflict" not in (_CONFIG / "chains" / "default.yaml").read_text()
        return
    [task] = handler.tasks
    assert (task.id, task.kind, task.profile) == ("resolve", tm.TaskKind.AGENT, "strong")
