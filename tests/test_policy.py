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


def test_budget_defaults_to_disabled_when_absent(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\n")
    pol = policy.load_policy(p)
    assert pol.budget.work_item_usd is None
    assert pol.budget.daily_usd is None


def test_budget_reads_both_caps(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\n"
        "budget:\n  work_item_usd: 20.0\n  daily_usd: 100\n"
    )
    pol = policy.load_policy(p)
    assert pol.budget.work_item_usd == 20.0
    # an int in the YAML is a legal dollar figure and becomes a float
    assert pol.budget.daily_usd == 100.0


def test_explicit_null_is_disabled_not_zero(tmp_path):
    """`null` means "no cap". Reading it as 0.0 would block every launch."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        "loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\n"
        "budget:\n  work_item_usd: null\n  daily_usd: null\n"
    )
    assert policy.load_policy(p).budget == policy.Budget()


def test_negative_budget_is_rejected_at_load(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\nbudget:\n  work_item_usd: -1\n"
    )
    with pytest.raises(policy.PolicyError, match="work_item_usd"):
        policy.load_policy(p)


def test_non_numeric_budget_is_rejected_at_load(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        'loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\nbudget:\n  daily_usd: "lots"\n'
    )
    with pytest.raises(policy.PolicyError, match="daily_usd"):
        policy.load_policy(p)


def test_budget_true_is_rejected_because_bool_is_an_int(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "loops: {}\ndefault: { attempts: 3, wall_clock_s: 3600 }\nbudget:\n  daily_usd: true\n"
    )
    with pytest.raises(policy.PolicyError, match="daily_usd"):
        policy.load_policy(p)


def test_a_policy_file_with_invalid_bytes_is_bad_config_not_a_crash(tmp_path):
    """`read_text()` raises `UnicodeDecodeError` — a `ValueError`, not an
    `OSError` and not a `yaml.YAMLError`. `lifespan` catches only `PolicyError`,
    so anything else escaping here refuses to boot the server."""
    p = tmp_path / "policy.yaml"
    p.write_bytes(b"default: { attempts: 3 }\nbudget: \xff\xfe\n")
    with pytest.raises(policy.PolicyError):
        policy.load_policy(p)


def test_escalate_after_is_parsed_onto_the_cap(tmp_path):
    """`escalate_after` is "how many cycles before a capability bump", the same
    kind of per-loop decision as "how many cycles before stopping", so it lives
    beside them (sub-project G spec §6)."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        "loops:\n  verify_fix_loop: { attempts: 5, wall_clock_s: 60, escalate_after: 2 }\n"
        "default: { attempts: 3, wall_clock_s: 60 }\n"
    )
    pol = policy.load_policy(p)
    assert pol.loops["verify_fix_loop"].escalate_after == 2
    # absent means no escalation, which is the pre-existing behaviour
    assert pol.default.escalate_after is None


@pytest.mark.parametrize("bad", ["0", "-1", "'2'", "true", "1.5"])
def test_escalate_after_must_be_a_positive_int(tmp_path, bad):
    p = tmp_path / "policy.yaml"
    p.write_text(
        f"loops:\n  verify_fix_loop: {{ attempts: 5, wall_clock_s: 60, escalate_after: {bad} }}\n"
        "default: { attempts: 3, wall_clock_s: 60 }\n"
    )
    with pytest.raises(policy.PolicyError, match="escalate_after"):
        policy.load_policy(p)
