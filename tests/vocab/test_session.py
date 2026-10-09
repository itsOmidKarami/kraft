import pytest

from kraft.vocab import (
    ADVANCING,
    AGENT_REPORTABLE,
    CAP_SWEEP_LEAVES,
    COMMAND_FINISHED,
    LIVE,
    UNFINISHED,
    UNREADABLE_EXIT,
    SessionStatus,
)

S = SessionStatus


@pytest.mark.parametrize(
    ("group", "members"),
    [
        (LIVE, {S.PENDING, S.RUNNING}),
        (UNFINISHED, {S.PENDING, S.RUNNING, S.PAUSED, S.WAITING, S.RATE_LIMITED, S.NEEDS_CONTEXT}),
        (ADVANCING, {S.DONE, S.DONE_WITH_CONCERNS}),
        (AGENT_REPORTABLE, {S.DONE, S.FAILED, S.DONE_WITH_CONCERNS, S.NEEDS_CONTEXT}),
        (CAP_SWEEP_LEAVES, {S.DONE, S.DONE_WITH_CONCERNS, S.CAPPED_OUT, S.WAITING}),
        (COMMAND_FINISHED, {S.DONE, S.FAILED}),
        (UNREADABLE_EXIT, {S.FAILED, S.UNKNOWN}),
    ],
    ids=[
        "live",
        "unfinished",
        "advancing",
        "agent_reportable",
        "cap_sweep_leaves",
        "command_finished",
        "unreadable_exit",
    ],
)
def test_a_group_is_the_set_the_audit_found(group, members):
    assert isinstance(group, tuple)
    assert set(group) == members


def test_the_check_order_is_the_declaration_order():
    assert [s.value for s in SessionStatus] == [
        "pending",
        "running",
        "done",
        "failed",
        "capped_out",
        "paused",
        "unknown",
        "done_with_concerns",
        "needs_context",
        "rate_limited",
        "config_error",
        "waiting",
        "conflict",
        "infra",
        "infra_stop",
    ]
