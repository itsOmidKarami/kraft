from kraft.vocab import FAILURE_KINDS, STOP_TRAITS, StopKind


def test_the_kinds_are_todays_twelve():
    assert [k.value for k in StopKind] == [
        "gate",
        "question",
        "cap",
        "budget",
        "failed",
        "conflict",
        "mr_closed",
        "config",
        "infra",
        "stuck",
        "wait",
        "rate_limit",
    ]


def test_failure_kinds_replace_the_board_tuple():
    assert set(FAILURE_KINDS) == {StopKind.FAILED, StopKind.CONFIG, StopKind.INFRA}


def test_gate_is_reported_but_never_stored():
    assert [k for k, t in STOP_TRAITS.items() if not t.stored] == [StopKind.GATE]
