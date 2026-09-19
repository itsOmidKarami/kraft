from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from kraft import policy

_SHIPPED = Path(__file__).parent.parent / "templates" / "policy.yaml"


def test_policy_input_rejects_wrong_scalar_bounds_and_shapes():
    """The YAML boundary validates static policy shape before runtime conversion."""
    base = {"default": {"attempts": 3, "wall_clock_s": 60}}
    with pytest.raises(ValidationError):
        policy.PolicyInput.model_validate({**base, "rate_limit_retries": True})
    with pytest.raises(ValidationError):
        policy.PolicyInput.model_validate({**base, "findings": {"loop_severities": ["urgent"]}})
    with pytest.raises(ValidationError):
        policy.PolicyInput.model_validate(
            {
                **base,
                "triggers": [
                    {"cron": "* * * * *", "repo": 1, "chain": "default", "title": "Sweep"}
                ],
            }
        )


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


def test_resolve_cap_override_replaces_only_named_fields(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text(
        "loops:\n  verify_fix_loop: { attempts: 5, wall_clock_s: 10 }\n"
        "default: { attempts: 2, wall_clock_s: 20 }\n"
    )
    p = policy.load_policy(d)
    assert policy.resolve_cap(p, "verify_fix_loop", {"attempts": 9}) == policy.Cap(9, 10)
    assert policy.resolve_cap(p, "verify_fix_loop", {"wall_clock_s": 99}) == policy.Cap(5, 99)
    assert policy.resolve_cap(p, "verify_fix_loop", {}) == policy.Cap(5, 10)
    assert policy.resolve_cap(p, "verify_fix_loop", None) == policy.Cap(5, 10)


@pytest.mark.parametrize(
    "doc",
    [
        "loops: {}\n",  # no default
        "default: { attempts: 0, wall_clock_s: 5 }\n",  # attempts < 1
        "default: { attempts: 3 }\n",  # missing wall_clock_s
        "default: not-a-mapping\n",
        "just a string\n",
        # loop_severities must be a list, not a bare scalar
        "default: {attempts: 3, wall_clock_s: 60}\nfindings: {loop_severities: critical}\n",
        # rate_limit_retries must be >= 1
        "default: { attempts: 2, wall_clock_s: 20 }\nrate_limit_retries: 0\n",
        # archive.after_days must be non-negative
        "default: { attempts: 3, wall_clock_s: 600 }\narchive: { after_days: -1 }\n",
        # max_concurrent must be >= 1
        "default: { attempts: 1, wall_clock_s: 1 }\nmax_concurrent: 0\n",
        # auto_escalate_stuck must be a bool
        "default: { attempts: 1, wall_clock_s: 1 }\nauto_escalate_stuck: maybe\n",
        # auto_escalate_stuck_cap must be >= 1
        "default: { attempts: 1, wall_clock_s: 1 }\nauto_escalate_stuck_cap: 0\n",
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


@pytest.mark.parametrize("key", ["loops", "findings", "triggers"])
def test_policy_null_collections_keep_their_legacy_empty_defaults(tmp_path, key):
    p = tmp_path / "policy.yaml"
    p.write_text(f"default: {{attempts: 3, wall_clock_s: 60}}\n{key}: null\n")
    loaded = policy.load_policy(p)
    assert loaded.loops == {}
    assert loaded.loop_severities == policy.DEFAULT_LOOP_SEVERITIES
    assert loaded.triggers == []


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


@pytest.mark.parametrize(
    ("budget_line", "match"),
    [
        ("work_item_usd: -1", "work_item_usd"),  # negative
        ('daily_usd: "lots"', "daily_usd"),  # non-numeric
        ("daily_usd: true", "daily_usd"),  # bool is an int in YAML, still rejected
    ],
)
def test_bad_budget_is_rejected_at_load(tmp_path, budget_line, match):
    p = tmp_path / "policy.yaml"
    p.write_text(
        f"loops: {{}}\ndefault: {{ attempts: 3, wall_clock_s: 3600 }}\nbudget:\n  {budget_line}\n"
    )
    with pytest.raises(policy.PolicyError, match=match):
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


def test_policy_defaults_rate_limit_retries_to_five():
    p = policy.Policy(loops={}, default=policy.Cap(attempts=3, wall_clock_s=100))
    assert p.rate_limit_retries == 5


def test_load_policy_reads_rate_limit_retries(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 2, wall_clock_s: 20 }\nrate_limit_retries: 8\n")
    p = policy.load_policy(d)
    assert p.rate_limit_retries == 8


def test_load_policy_defaults_rate_limit_retries_when_absent(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 2, wall_clock_s: 20 }\n")
    p = policy.load_policy(d)
    assert p.rate_limit_retries == 5


def test_load_shipped_policy_has_rate_limit_retries():
    p = policy.load_policy(_SHIPPED)
    assert isinstance(p.rate_limit_retries, int) and p.rate_limit_retries >= 1


def test_load_policy_reads_archive_after_days(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 3, wall_clock_s: 600 }\narchive: { after_days: 30 }\n")
    p = policy.load_policy(d)
    assert p.archive_after_days == 30


def test_load_policy_defaults_archive_after_days_to_none(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 3, wall_clock_s: 600 }\n")
    p = policy.load_policy(d)
    assert p.archive_after_days is None


def test_load_policy_parses_triggers(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text(
        "default: { attempts: 2, wall_clock_s: 20 }\n"
        "triggers:\n"
        '  - cron: "0 * * * *"\n'
        "    repo: /repo\n"
        "    chain: default\n"
        "    title: Nightly sweep\n"
        "    description: sweep it\n"
    )
    p = policy.load_policy(d)
    assert len(p.triggers) == 1
    t = p.triggers[0]
    assert t == policy.Trigger(
        cron="0 * * * *",
        repo="/repo",
        chain="default",
        title="Nightly sweep",
        description="sweep it",
    )


def test_load_policy_defaults_triggers_to_empty(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 2, wall_clock_s: 20 }\n")
    assert policy.load_policy(d).triggers == []


@pytest.mark.parametrize(
    "doc",
    [
        # missing required field
        "default: { attempts: 2, wall_clock_s: 20 }\n"
        'triggers:\n  - cron: "* * * * *"\n    repo: /r\n    chain: c\n',
        # cron has wrong field count
        "default: { attempts: 2, wall_clock_s: 20 }\n"
        'triggers:\n  - cron: "* * *"\n    repo: /r\n    chain: c\n    title: t\n',
        # cron uses an unsupported range
        "default: { attempts: 2, wall_clock_s: 20 }\n"
        'triggers:\n  - cron: "1-5 * * * *"\n    repo: /r\n    chain: c\n    title: t\n',
        # triggers not a list
        "default: { attempts: 2, wall_clock_s: 20 }\ntriggers: nope\n",
    ],
)
def test_load_policy_rejects_malformed_triggers(tmp_path, doc):
    d = tmp_path / "policy.yaml"
    d.write_text(doc)
    with pytest.raises(policy.PolicyError):
        policy.load_policy(d)


def test_cron_due_matches_exact_fields():
    dt = datetime(2026, 9, 10, 14, 30, tzinfo=UTC)  # a Thursday
    assert policy.cron_due("30 14 10 9 *", dt)
    assert not policy.cron_due("31 14 10 9 *", dt)
    assert not policy.cron_due("30 15 10 9 *", dt)


def test_cron_due_matches_star_and_lists():
    dt = datetime(2026, 9, 10, 14, 30, tzinfo=UTC)
    assert policy.cron_due("* * * * *", dt)
    assert policy.cron_due("30,45 14 * * *", dt)
    assert not policy.cron_due("15,45 14 * * *", dt)


def test_load_policy_reads_max_concurrent(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 1, wall_clock_s: 1 }\nmax_concurrent: 7\n")
    assert policy.load_policy(d).max_concurrent == 7


def test_load_policy_defaults_max_concurrent_to_three(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 1, wall_clock_s: 1 }\n")
    assert policy.load_policy(d).max_concurrent == 3


def test_load_policy_falls_back_to_legacy_intake_max_concurrent(tmp_path):
    (tmp_path / "intake.yaml").write_text("max_concurrent: 9\n")
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 1, wall_clock_s: 1 }\n")
    assert policy.load_policy(d).max_concurrent == 9


def test_load_policy_defaults_auto_escalate_stuck_on(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 1, wall_clock_s: 1 }\n")
    p = policy.load_policy(d)
    assert p.auto_escalate_stuck is True
    assert p.auto_escalate_stuck_cap == 3


def test_load_policy_reads_auto_escalate_stuck(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text(
        "default: { attempts: 1, wall_clock_s: 1 }\n"
        "auto_escalate_stuck: false\n"
        "auto_escalate_stuck_cap: 5\n"
    )
    p = policy.load_policy(d)
    assert p.auto_escalate_stuck is False
    assert p.auto_escalate_stuck_cap == 5


def test_load_policy_rejects_non_bool_auto_escalate_stuck(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 1, wall_clock_s: 1 }\nauto_escalate_stuck: maybe\n")
    with pytest.raises(policy.PolicyError):
        policy.load_policy(d)


def test_load_policy_rejects_bad_auto_escalate_stuck_cap(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 1, wall_clock_s: 1 }\nauto_escalate_stuck_cap: 0\n")
    with pytest.raises(policy.PolicyError):
        policy.load_policy(d)


def test_load_policy_defaults_auto_escalate_delay_s_to_zero(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 1, wall_clock_s: 1 }\n")
    p = policy.load_policy(d)
    assert p.auto_escalate_delay_s == 0


def test_load_policy_reads_auto_escalate_delay_s(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 1, wall_clock_s: 1 }\nauto_escalate_delay_s: 120\n")
    p = policy.load_policy(d)
    assert p.auto_escalate_delay_s == 120


def test_load_policy_rejects_negative_auto_escalate_delay_s(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 1, wall_clock_s: 1 }\nauto_escalate_delay_s: -1\n")
    with pytest.raises(policy.PolicyError):
        policy.load_policy(d)


def test_load_policy_rejects_non_int_auto_escalate_delay_s(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 1, wall_clock_s: 1 }\nauto_escalate_delay_s: soon\n")
    with pytest.raises(policy.PolicyError):
        policy.load_policy(d)


def test_a_cap_rejects_a_zero_attempt_count():
    """`load_policy` checked this; it is now a field constraint that cannot be
    bypassed by constructing a Cap directly, which the dataclass allowed."""
    with pytest.raises(Exception):  # noqa: B017 -- pydantic's ValidationError
        policy.Cap(attempts=0, wall_clock_s=60)


def test_load_policy_reads_forge_cli_timeout_s_and_defaults_it(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 1, wall_clock_s: 1 }\n")
    assert policy.load_policy(d).forge_cli_timeout_s == 120.0
    d.write_text("default: { attempts: 1, wall_clock_s: 1 }\nforge_cli_timeout_s: 30\n")
    assert policy.load_policy(d).forge_cli_timeout_s == 30.0
    d.write_text("default: { attempts: 1, wall_clock_s: 1 }\nforge_cli_timeout_s: 0\n")
    with pytest.raises(policy.PolicyError):
        policy.load_policy(d)
