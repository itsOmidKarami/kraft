"""`gate_review.review` for a gate whose reviewer only checks first
(`auto_review_mode: checks_first`): it may reject, or hand the gate to the
person with `pass`, and never approve.

Beside `tests/skills/test_gate_review.py`, which is at its line budget; its
helpers are reused, and `run_agent_task` is monkeypatched the same way.
"""

from __future__ import annotations

import pytest
from skills.test_gate_review import _claude_profile, _fake_agent, _gate, _seed  # noqa: F401

from kraft import executor, gate_review
from kraft.templates.models import CHECKS_FIRST


async def _review(monkeypatch, database, run_dirs, result, *, mode=CHECKS_FIRST):
    """One review of `_seed`'s gate, its reviewer in `mode` (None: unset,
    which is `decides`). Returns `(verdict, note, seen)`."""
    seen = {}
    monkeypatch.setattr("kraft.gate_review._agent.run_agent_task", _fake_agent(result, seen))
    await _seed(database, run_dirs, "w1")
    verdict, note = await gate_review.review(
        database,
        run_dirs,
        work_item_id="w1",
        gate="spec_approval",
        node=_gate(run_dirs, **({"auto_review_mode": mode} if mode else {})),
        launch=executor.LaunchContext(repo_entry=None, skills_dir=None),
    )
    return verdict, note, seen


@pytest.mark.parametrize(
    "mode,result,expected",
    [
        # The reviewer meant "good"; the person still decides.
        pytest.param(
            CHECKS_FIRST, {"status": "done", "verdict": "approve"}, "pass", id="approve-is-pass"
        ),
        pytest.param(
            CHECKS_FIRST, {"verdict": "pass", "concerns": "read it all"}, "pass", id="pass"
        ),
        pytest.param(CHECKS_FIRST, {"verdict": "pass"}, "pass", id="pass-without-concerns"),
        pytest.param(
            CHECKS_FIRST,
            {"verdict": "reject", "concerns": "redo the migration"},
            "reject",
            id="reject",
        ),
        pytest.param(
            CHECKS_FIRST,
            {"verdict": "reject", "concerns": ""},
            "undecided",
            id="reject-without-concerns",
        ),
        # It may not edit, so it has nothing to have fixed.
        pytest.param(
            CHECKS_FIRST, {"verdict": "fixed", "concerns": "fixed it"}, "undecided", id="fixed"
        ),
        pytest.param(CHECKS_FIRST, {"verdict": "undecided"}, "undecided", id="undecided"),
        pytest.param(CHECKS_FIRST, {"verdict": "APPROVE!"}, "undecided", id="unknown"),
        pytest.param(
            CHECKS_FIRST,
            {"status": "failed", "verdict": "pass"},
            "undecided",
            id="failed-session",
        ),
        pytest.param(CHECKS_FIRST, None, "undecided", id="no-result"),
        # `pass` exists only for a reviewer that checks first.
        pytest.param(
            None, {"status": "done", "verdict": "pass"}, "undecided", id="decides-has-no-pass"
        ),
    ],
)
async def test_checks_first_verdict_resolution(
    monkeypatch, database, run_dirs, mode, result, expected
):
    verdict, note, _seen = await _review(monkeypatch, database, run_dirs, result, mode=mode)
    assert (verdict, note) == (expected, (result or {}).get("concerns", ""))


@pytest.mark.parametrize(
    "mode,offered,withheld,denies_edits",
    [
        pytest.param(
            CHECKS_FIRST,
            ('"verdict": "reject"', '"verdict": "pass"'),
            ('"verdict": "approve"', '"verdict": "fixed"'),
            True,
            id="checks-first",
        ),
        pytest.param(
            None,
            ('"verdict": "approve"', '"verdict": "fixed"', '"verdict": "reject"'),
            ('"verdict": "pass"',),
            False,
            id="decides",
        ),
    ],
)
async def test_checks_first_instruction_offers_only_reject_and_pass(
    monkeypatch, database, run_dirs, mode, offered, withheld, denies_edits
):
    *_, seen = await _review(
        monkeypatch, database, run_dirs, {"verdict": "pass", "concerns": "ok"}, mode=mode
    )
    instruction = seen["kwargs"]["task_instruction"]
    assert all(v in instruction for v in offered), instruction
    assert not any(v in instruction for v in withheld), instruction
    deny = seen["kwargs"]["deny_tools"]
    assert ("Edit" in deny, "NotebookEdit" in deny) == (denies_edits, denies_edits), deny
