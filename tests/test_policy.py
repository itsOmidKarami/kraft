from pathlib import Path

import pytest

from kraft import policy

_SHIPPED = Path(__file__).parent.parent / "templates" / "policy.yaml"


def test_load_shipped_policy():
    p = policy.load_policy(_SHIPPED)
    assert "verify_fix_loop" in p.loops
    c = p.loops["verify_fix_loop"]
    assert isinstance(c.attempts, int) and c.attempts >= 1
    assert isinstance(c.wall_clock_s, int) and c.wall_clock_s >= 1
    assert isinstance(p.default, policy.Cap)


def test_resolve_cap_falls_back_to_default(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text(
        "loops:\n  verify_fix_loop: { attempts: 5, wall_clock_s: 10 }\n"
        "default: { attempts: 2, wall_clock_s: 20 }\n"
    )
    p = policy.load_policy(d)
    assert policy.resolve_cap(p, "verify_fix_loop") == policy.Cap(5, 10)
    assert policy.resolve_cap(p, "nonexistent_loop") == policy.Cap(2, 20)


@pytest.mark.parametrize(
    "doc",
    [
        "loops: {}\n",  # no default
        "default: { attempts: 0, wall_clock_s: 5 }\n",  # attempts < 1
        "default: { attempts: 3 }\n",  # missing wall_clock_s
        "default: not-a-mapping\n",
        "just a string\n",
    ],
)
def test_load_policy_rejects_malformed(tmp_path, doc):
    d = tmp_path / "policy.yaml"
    d.write_text(doc)
    with pytest.raises(policy.PolicyError):
        policy.load_policy(d)


def test_check_attempts_breach():
    cap = policy.Cap(attempts=3, wall_clock_s=99999)
    now = "2026-09-02T00:00:00+00:00"
    started = "2026-09-02T00:00:00+00:00"
    assert policy.check(count=3, started_at=started, cap=cap, now=now) == "ok"
    assert policy.check(count=4, started_at=started, cap=cap, now=now) == "breached"


def test_check_wall_clock_breach():
    cap = policy.Cap(attempts=99, wall_clock_s=60)
    started = "2026-09-02T00:00:00+00:00"
    within = "2026-09-02T00:00:59+00:00"
    past = "2026-09-02T00:01:00+00:00"
    assert policy.check(count=1, started_at=started, cap=cap, now=within) == "ok"
    assert policy.check(count=1, started_at=started, cap=cap, now=past) == "breached"


def test_loop_severities_default(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("default: {attempts: 3, wall_clock_s: 60}\n")
    assert policy.load_policy(p).loop_severities == frozenset({"critical", "important"})


def test_loop_severities_configured(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "default: {attempts: 3, wall_clock_s: 60}\nfindings: {loop_severities: [critical]}\n"
    )
    assert policy.load_policy(p).loop_severities == frozenset({"critical"})


def test_loop_severities_all_three_restores_old_behaviour(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "default: {attempts: 3, wall_clock_s: 60}\n"
        "findings: {loop_severities: [critical, important, minor]}\n"
    )
    assert policy.load_policy(p).loop_severities == frozenset({"critical", "important", "minor"})


def test_unknown_severity_is_rejected(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "default: {attempts: 3, wall_clock_s: 60}\n"
        "findings: {loop_severities: [critical, urgent]}\n"
    )
    with pytest.raises(policy.PolicyError, match="urgent"):
        policy.load_policy(p)


def test_loop_severities_must_be_a_list(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "default: {attempts: 3, wall_clock_s: 60}\nfindings: {loop_severities: critical}\n"
    )
    with pytest.raises(policy.PolicyError):
        policy.load_policy(p)
