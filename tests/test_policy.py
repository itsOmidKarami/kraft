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
    c = p.default
    assert isinstance(c, policy.Cap)
    assert isinstance(c.attempts, int) and c.attempts >= 1
    assert isinstance(c.wall_clock_s, int) and c.wall_clock_s >= 1


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


_BASE = "default: { attempts: 1, wall_clock_s: 1 }\n"


@pytest.mark.parametrize(
    "doc",
    [
        pytest.param("loops: {}\n", id="no-default"),
        pytest.param("default: { attempts: 0, wall_clock_s: 5 }\n", id="attempts-below-1"),
        pytest.param("default: { attempts: 3 }\n", id="missing-wall-clock"),
        pytest.param("default: not-a-mapping\n", id="default-not-a-mapping"),
        pytest.param("just a string\n", id="not-a-mapping"),
        pytest.param(_BASE + "findings: {loop_severities: critical}\n", id="severities-not-a-list"),
        pytest.param(_BASE + "rate_limit_retries: 0\n", id="rate-limit-retries-below-1"),
        pytest.param(_BASE + "archive: { after_days: -1 }\n", id="archive-after-days-negative"),
        pytest.param(_BASE + "max_concurrent: 0\n", id="max-concurrent-below-1"),
        pytest.param(_BASE + "auto_escalate_stuck: maybe\n", id="auto-escalate-stuck-not-bool"),
        pytest.param(_BASE + "auto_escalate_stuck_cap: 0\n", id="auto-escalate-stuck-cap-below-1"),
        pytest.param(_BASE + "auto_escalate_delay_s: -1\n", id="auto-escalate-delay-negative"),
        pytest.param(_BASE + "auto_escalate_delay_s: soon\n", id="auto-escalate-delay-not-int"),
        pytest.param(_BASE + "forge_cli_timeout_s: 0\n", id="forge-cli-timeout-zero"),
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


def test_a_loop_cap_refuses_the_retired_escalate_after(tmp_path):
    """`escalate_after` bumped a legacy hook to its `escalate_model` after N fix
    cycles. V1 has no such field on any task; a stuck node escalates through its
    declared `escalation` task instead (`stuck-escalation-is-an-exec-node-
    control`). A key that binds nothing is refused rather than silently kept."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        "loops:\n  build.fix_loop: { attempts: 5, wall_clock_s: 60, escalate_after: 2 }\n"
        "default: { attempts: 3, wall_clock_s: 60 }\n"
    )
    with pytest.raises(policy.PolicyError, match="escalate_after"):
        policy.load_policy(p)


def test_policy_defaults_rate_limit_retries_to_five():
    p = policy.Policy(loops={}, default=policy.Cap(attempts=3, wall_clock_s=100))
    assert p.rate_limit_retries == 5


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


@pytest.mark.parametrize(
    ("tail", "attr", "expected"),
    [
        pytest.param("rate_limit_retries: 8\n", "rate_limit_retries", 8, id="rate-limit-retries"),
        pytest.param("", "rate_limit_retries", 5, id="rate-limit-retries-default"),
        pytest.param(
            "archive: { after_days: 30 }\n", "archive_after_days", 30, id="archive-after-days"
        ),
        pytest.param("", "archive_after_days", None, id="archive-after-days-default"),
        pytest.param("max_concurrent: 7\n", "max_concurrent", 7, id="max-concurrent"),
        pytest.param("", "max_concurrent", 3, id="max-concurrent-default"),
        pytest.param(
            "auto_escalate_stuck: false\n", "auto_escalate_stuck", False, id="auto-escalate-stuck"
        ),
        pytest.param("", "auto_escalate_stuck", True, id="auto-escalate-stuck-default"),
        pytest.param(
            "auto_escalate_stuck_cap: 5\n",
            "auto_escalate_stuck_cap",
            5,
            id="auto-escalate-stuck-cap",
        ),
        pytest.param("", "auto_escalate_stuck_cap", 3, id="auto-escalate-stuck-cap-default"),
        pytest.param(
            "auto_escalate_delay_s: 120\n", "auto_escalate_delay_s", 120, id="auto-escalate-delay"
        ),
        pytest.param("", "auto_escalate_delay_s", 0, id="auto-escalate-delay-default"),
        pytest.param(
            "forge_cli_timeout_s: 30\n", "forge_cli_timeout_s", 30.0, id="forge-cli-timeout"
        ),
        pytest.param("", "forge_cli_timeout_s", 120.0, id="forge-cli-timeout-default"),
        pytest.param("", "triggers", [], id="triggers-default"),
    ],
)
def test_load_policy_reads_scalar(tmp_path, tail, attr, expected):
    """A top-level policy.yaml scalar is read as written, or defaulted when absent."""
    d = tmp_path / "policy.yaml"
    d.write_text(_BASE + tail)
    assert getattr(policy.load_policy(d), attr) == expected


def test_load_policy_falls_back_to_legacy_intake_max_concurrent(tmp_path):
    (tmp_path / "intake.yaml").write_text("max_concurrent: 9\n")
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 1, wall_clock_s: 1 }\n")
    assert policy.load_policy(d).max_concurrent == 9


def test_a_cap_rejects_a_zero_attempt_count():
    """`load_policy` checked this; it is now a field constraint that cannot be
    bypassed by constructing a Cap directly, which the dataclass allowed."""
    with pytest.raises(Exception):  # noqa: B017 -- pydantic's ValidationError
        policy.Cap(attempts=0, wall_clock_s=60)


# ── model-led boundary: PolicyInput.from_yaml / Policy.from_input / cap_for ──


def test_the_target_shape_round_trips(tmp_path):
    """`PolicyInput.from_yaml` reads and validates, `Policy.from_input`
    converts, `Policy.cap_for` looks up -- the exact shape the plan pins."""
    d = tmp_path / "policy.yaml"
    d.write_text(
        "loops:\n  ci_wait: { attempts: 5, wall_clock_s: 10 }\n"
        "default: { attempts: 2, wall_clock_s: 20 }\n"
    )
    parsed = policy.PolicyInput.from_yaml(d)
    assert isinstance(parsed, policy.PolicyInput)
    pol = policy.Policy.from_input(parsed, source=d)
    assert isinstance(pol, policy.Policy)
    cap = pol.cap_for("ci_wait", None)
    assert cap == policy.Cap(5, 10)


def test_load_policy_and_resolve_cap_still_work_as_two_line_delegators(tmp_path):
    """The 15/11 existing callers (`executor/`, `ci_wait.py`, ...) are not
    migrated in this phase; `load_policy`/`resolve_cap` must keep behaving
    exactly as before."""
    d = tmp_path / "policy.yaml"
    d.write_text(
        "loops:\n  ci_wait: { attempts: 5, wall_clock_s: 10 }\n"
        "default: { attempts: 2, wall_clock_s: 20 }\n"
    )
    pol = policy.load_policy(d)
    assert policy.resolve_cap(pol, "ci_wait", {"attempts": 9}) == policy.Cap(9, 10)
    assert policy.resolve_cap(pol, "nonexistent") == policy.Cap(2, 20)


def test_from_yaml_rejects_malformed_policy_the_same_way(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 0, wall_clock_s: 5 }\n")
    with pytest.raises(policy.PolicyError):
        policy.PolicyInput.from_yaml(d)


def test_cap_for_accepts_a_raw_dict_override_at_the_boundary():
    """A stored node override arrives as an untyped dict
    (`store.node_overrides_of`); `cap_for` validates it internally into a
    `CapOverride` rather than requiring every caller to construct one."""
    pol = policy.Policy(loops={}, default=policy.Cap(attempts=3, wall_clock_s=60))
    assert pol.cap_for("default", {"attempts": 9}) == policy.Cap(9, 60)
    assert pol.cap_for("default", policy.CapOverride(attempts=9)) == policy.Cap(9, 60)


def test_cap_for_ignores_unrelated_keys_on_a_stored_node_override():
    """A `node_overrides` row also carries `model`/`effort`/`auto_escalate`,
    validated elsewhere -- `cap_for` must not choke on them."""
    pol = policy.Policy(loops={}, default=policy.Cap(attempts=3, wall_clock_s=60))
    assert pol.cap_for("default", {"attempts": 9, "effort": "high"}) == policy.Cap(9, 60)


def test_cron_fields_is_a_named_type_not_an_anonymous_tuple():
    fields = policy._cron_fields("cron", "30 14 10 9 *")
    assert isinstance(fields, policy.CronFields)
    assert (fields.minute, fields.hour, fields.day, fields.month, fields.weekday) == (
        "30",
        "14",
        "10",
        "9",
        "*",
    )


# ── V1 instance policy: defaults, administrator maxima, layered overrides ──


@pytest.fixture
def instance_policy() -> policy.InstancePolicy:
    parsed = policy.InstancePolicyInput.model_validate(
        {
            "defaults": {
                "timeout_minutes": 60,
                "max_attempts": 3,
                "allowed_harnesses": ["codex", "claude"],
            },
            "maxima": {
                "work_item": {"token_budget": 2_000_000},
                "allowed_tools": ["git", "shell", "pytest"],
                "allowed_harnesses": ["codex", "claude"],
            },
        }
    )
    return policy.InstancePolicy.from_input(parsed)


def test_template_policy_cannot_widen_allowed_tools(instance_policy):
    with pytest.raises(policy.PolicyError, match="allowed_tools"):
        instance_policy.apply_template_override({"allowed_tools": ["shell", "network"]})


def test_template_policy_can_narrow_allowed_tools(instance_policy):
    tightened = instance_policy.apply_template_override({"allowed_tools": ["git"]})
    assert tightened.allowed_tools == ("git",)


def test_template_policy_cannot_exceed_token_budget_ceiling(instance_policy):
    with pytest.raises(policy.PolicyError, match="token_budget"):
        instance_policy.apply_template_override({"token_budget": 3_000_000})


def test_token_budget_moves_within_the_maximum_not_the_inherited_value(instance_policy):
    """Ruling 198: a budget is a scope's own cap, bounded here by `maxima`
    alone, as an operational value is. That a nested scope's stays at or under
    its parent's is `ResolvedChain._check_caps`, which names both scopes."""
    narrowed = instance_policy.apply_template_override({"token_budget": 1_000_000})
    assert narrowed.token_budget == 1_000_000
    assert narrowed.apply_template_override({"token_budget": 1_500_000}).token_budget == 1_500_000
    with pytest.raises(policy.PolicyError, match="token_budget"):
        narrowed.apply_template_override({"token_budget": 3_000_000})


# ── allowed_harnesses: operational-with-an-administrator-maximum, not a
# ratchet-only safety field (docs/templates-v1-design.md lists it under both
# `defaults:` and `maxima:`, unlike `allowed_tools`/`token_budget`) ──


def test_template_policy_can_narrow_allowed_harnesses(instance_policy):
    narrowed = instance_policy.apply_template_override({"allowed_harnesses": ["codex"]})
    assert narrowed.allowed_harnesses == ("codex",)


def test_template_policy_can_widen_allowed_harnesses_within_maximum(instance_policy):
    """Unlike `allowed_tools`, `allowed_harnesses` may widen -- as long as it
    stays within `maxima.allowed_harnesses`."""
    widened = instance_policy.apply_template_override({"allowed_harnesses": ["codex", "claude"]})
    assert set(widened.allowed_harnesses) == {"codex", "claude"}


def test_template_policy_cannot_widen_allowed_harnesses_past_maximum(instance_policy):
    with pytest.raises(policy.PolicyError, match="allowed_harnesses"):
        instance_policy.apply_template_override({"allowed_harnesses": ["codex", "gemini"]})


# ── maxima means maxima: `defaults:` is bounded by `maxima:` too
# (policy-has-defaults-and-administrator-maxima) ──


def test_default_timeout_above_its_maximum_is_rejected():
    with pytest.raises(ValidationError, match="defaults.timeout_minutes 500"):
        policy.InstancePolicyInput.model_validate(
            {"defaults": {"timeout_minutes": 500}, "maxima": {"timeout_minutes": 60}}
        )


def test_default_max_attempts_above_its_maximum_is_rejected():
    with pytest.raises(ValidationError, match="defaults.max_attempts 9"):
        policy.InstancePolicyInput.model_validate(
            {"defaults": {"max_attempts": 9}, "maxima": {"max_attempts": 5}}
        )


def test_default_harnesses_outside_its_maximum_are_rejected():
    with pytest.raises(ValidationError, match="defaults.allowed_harnesses"):
        policy.InstancePolicyInput.model_validate(
            {
                "defaults": {"allowed_harnesses": ["codex", "gemini"]},
                "maxima": {"allowed_harnesses": ["codex"]},
            }
        )


def test_unset_harness_maximum_bounds_nothing():
    """An unset maximum is no bound at all -- a decision, not an omission: any
    `defaults:` list is accepted and any override may name any harness."""
    pol = policy.InstancePolicy.from_input(
        policy.InstancePolicyInput.model_validate(
            {"defaults": {"allowed_harnesses": ["codex"], "timeout_minutes": 500}}
        )
    )
    widened = pol.apply_template_override({"allowed_harnesses": ["anything_at_all"]})
    assert widened.allowed_harnesses == ("anything_at_all",)


def test_defaults_narrower_than_maxima_can_still_widen_back_to_maxima():
    """Regression: `InstancePolicy.from_input` seeds the `allowed_harnesses`
    ceiling from `defaults`, which may be narrower than `maxima`. Treating
    `allowed_harnesses` as ratchet-only against that seeded value would
    permanently lower the real ceiling below what the administrator actually
    allowed -- an operator could never widen back toward `maxima`."""
    parsed = policy.InstancePolicyInput.model_validate(
        {
            "defaults": {"allowed_harnesses": ["codex"]},
            "maxima": {"allowed_harnesses": ["codex", "claude"]},
        }
    )
    pol = policy.InstancePolicy.from_input(parsed)
    assert pol.allowed_harnesses == ("codex",)
    widened = pol.apply_template_override({"allowed_harnesses": ["codex", "claude"]})
    assert set(widened.allowed_harnesses) == {"codex", "claude"}


def test_template_policy_may_replace_operational_defaults_either_direction(instance_policy):
    """`timeout_minutes` has no configured administrator maximum here, so it
    may move up or down freely (`template-policy-may-replace-operational-
    defaults`)."""
    raised = instance_policy.apply_template_override({"timeout_minutes": 120})
    assert raised.timeout_minutes == 120
    lowered = instance_policy.apply_template_override({"timeout_minutes": 5})
    assert lowered.timeout_minutes == 5


def test_work_item_policy_may_exceed_default_within_admin_maximum():
    """An explicit administrator maximum on an operational field caps how far
    a narrower-scope override may raise it
    (`work-item-policy-may-exceed-default-ceilings-within-admin-maximum`)."""
    parsed = policy.InstancePolicyInput.model_validate(
        {"defaults": {"max_attempts": 3}, "maxima": {"max_attempts": 5}}
    )
    pol = policy.InstancePolicy.from_input(parsed)
    within = pol.apply_template_override({"max_attempts": 5})
    assert within.max_attempts == 5
    with pytest.raises(policy.PolicyError, match="max_attempts"):
        pol.apply_template_override({"max_attempts": 6})


def test_policy_override_rejects_unknown_fields_field_specifically():
    """Overrides are validated field by field, not merged as an unrestricted
    generic dict (`policy-override-rules-are-field-specific`)."""
    with pytest.raises(Exception):  # noqa: B017 -- pydantic's ValidationError
        policy.TemplatePolicyOverride.model_validate({"not_a_real_field": 1})


def test_policy_overrides_compose_and_a_narrower_layer_cannot_widen_a_broader_one(instance_policy):
    """The same override mechanism composes, broadest to narrowest
    (`policy-is-layered-by-execution-scope`): a second override applied to the
    result of a first cannot hand back room the first already closed. The
    layers here are anonymous on purpose. No Repository policy layer exists yet
    (`repository-policy-cannot-relax-instance-safety` is unpinned, Kraft-jzv1l),
    so this test names none."""
    outer = instance_policy.apply_template_override({"allowed_tools": ["git", "shell"]})
    inner = outer.apply_template_override({"allowed_tools": ["git"]})
    assert inner.allowed_tools == ("git",)
    with pytest.raises(policy.PolicyError, match="allowed_tools"):
        outer.apply_template_override({"allowed_tools": ["git", "shell", "pytest"]})


# --- V1 `defaults:`/`maxima:` through the one policy.yaml loader (Ruling 37) ---


def test_instance_policy_loads_defaults_and_maxima_from_yaml(tmp_path):
    """`policy.yaml` carries V1's `defaults:`/`maxima:` sections, and the
    *existing* loader reads them. Before this they were `extra`-ignored: an
    administrator ceiling written in the file bound nothing."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        "default: { attempts: 3, wall_clock_s: 60 }\n"
        "defaults:\n"
        "  timeout_minutes: 30\n"
        "  allowed_harnesses: [codex]\n"
        "maxima:\n"
        "  timeout_minutes: 90\n"
        "  work_item: { token_budget: 500000 }\n"
        "  allowed_tools: [git, shell]\n"
        "  allowed_harnesses: [codex, claude]\n"
    )
    parsed = policy.PolicyInput.from_yaml(p)
    assert parsed.defaults.timeout_minutes == 30
    assert parsed.maxima.work_item.token_budget == 500000

    resolved = parsed.instance_policy()
    assert resolved.timeout_minutes == 30
    # A cap with no `defaults:` entry runs at its level's maximum (Ruling 211).
    assert resolved.at_level("work_item").token_budget == 500000
    assert resolved.allowed_tools == ("git", "shell")
    # Operational-with-a-maximum: widening back up to `maxima` is allowed.
    assert resolved.apply_template_override(
        {"allowed_harnesses": ["codex", "claude"]}
    ).allowed_harnesses == ("codex", "claude")


@pytest.mark.parametrize(
    ("tail", "names"),
    [
        # The coherence rule fires where the file is read, not at first use --
        # otherwise a ceiling the administrator wrote is only discovered by the
        # dispatch that violates it.
        (
            "defaults: { timeout_minutes: 120 }\nmaxima: { timeout_minutes: 90 }\n",
            "timeout_minutes",
        ),
        # `PolicyError`, never a raw `OSError`/`YAMLError`/`ValidationError`, and
        # it says which file -- `lifespan` catches only the former.
        ("defaults: [not, a, mapping]\n", "policy.yaml"),
        # Kraft-sz4dh: `maximum:` for `maxima:` used to load cleanly and bound
        # nothing. An unknown top-level key is refused, naming the key.
        ("maximum: { allowed_tools: [git] }\n", "unknown key 'maximum'"),
    ],
    ids=["defaults-past-maxima", "malformed-names-its-file", "misspelled-top-level-key"],
)
def test_a_bad_policy_yaml_is_refused_at_load_naming_why(tmp_path, tail, names):
    p = tmp_path / "policy.yaml"
    p.write_text("default: { attempts: 3, wall_clock_s: 60 }\n" + tail)
    with pytest.raises(policy.PolicyError, match=names):
        policy.PolicyInput.from_yaml(p)


def test_policy_yaml_has_exactly_one_loader():
    """Ruling 18/37: one filename, one live loader. A second `from_yaml` over
    `policy.yaml` is how two readers of one file start disagreeing -- so
    `InstancePolicyInput` deliberately has none, and the V1 sections ride on
    the loader that already existed."""
    assert hasattr(policy.PolicyInput, "from_yaml")
    assert not hasattr(policy.InstancePolicyInput, "from_yaml")


def test_the_seeded_policy_yaml_names_no_loop_that_binds_nothing():
    """Ruling 57. A `loops:` key naming no live loop is *silently* unused --
    `Policy.cap_for` is `self.loops.get(key, self.default)`, no error and no
    warning -- so a stale cap in the seed looks live and binds nothing. The V1
    fix loop's key is `walk._loop_key(node)`, which is per-node, so the seed
    names none; and `ci_wait` went with the poller that read it (Task 9: a
    wait's timeout is its task's own `wait:`)."""
    parsed = policy.PolicyInput.from_yaml(_SHIPPED)
    assert not parsed.loops, parsed.loops


def test_the_shipped_policy_yaml_has_no_unknown_key():
    """The refusal above must not refuse the seed a fresh install copies."""
    parsed = policy.PolicyInput.from_yaml(
        Path(__file__).resolve().parents[1] / "templates" / "policy.yaml"
    )
    # A real field off the shipped file, not just "it parsed": extra="forbid"
    # would have raised on an unknown key, but a bare no-raise wouldn't prove
    # the loader actually read the file's contents rather than a stub.
    assert parsed.default.attempts == 3


# ── Ruling 105: `deny_tools` and `sandbox` are permission-shaped safety fields,
# tightened like `allowed_tools` -- a narrower layer can add to them, never
# take away ──

_SANDBOX = {"kind": "docker", "image": "kraft/worker:1"}


def test_deny_tools_only_accumulate_down_the_layers(instance_policy):
    """A deny list narrows what `allowed_tools` permits, so a narrower layer
    adds to it and can never drop an inherited entry."""
    outer = instance_policy.apply_template_override({"deny_tools": ["WebFetch"]})
    inner = outer.apply_template_override({"deny_tools": ["Bash", "WebFetch"]})
    assert inner.deny_tools == ("WebFetch", "Bash")
    assert outer.apply_template_override({"deny_tools": []}).deny_tools == ("WebFetch",)


def test_a_sandbox_once_set_cannot_be_changed_by_a_narrower_layer(instance_policy):
    sandboxed = instance_policy.apply_template_override({"sandbox": _SANDBOX})
    assert sandboxed.sandbox is not None and sandboxed.sandbox.image == "kraft/worker:1"
    assert sandboxed.apply_template_override({"sandbox": _SANDBOX}).sandbox == sandboxed.sandbox
    with pytest.raises(policy.PolicyError, match="sandbox") as refused:
        sandboxed.apply_template_override({"sandbox": {"kind": "docker", "image": "other"}})
    assert refused.value.field == "sandbox"


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"allowed_tools": ["network"]}, "allowed_tools"),
        ({"token_budget": 3_000_000}, "token_budget"),
        ({"allowed_harnesses": ["gemini"]}, "allowed_harnesses"),
    ],
    ids=["allowed_tools", "token_budget", "allowed_harnesses"],
)
def test_a_policy_refusal_names_the_field_it_refused(instance_policy, override, field):
    """`policy-override-rules-are-field-specific`: a refusal says which field,
    as data, so a retry override's error can point at it."""
    with pytest.raises(policy.PolicyError) as refused:
        instance_policy.apply_template_override(override)
    assert refused.value.field == field


def test_a_task_scope_override_refuses_the_loop_bounds():
    """`max_attempts` and `timeout_minutes` bound an execution node's fix loop
    and nothing a task, step or gate runs, so those scopes refuse them at load
    rather than accept a value nothing reads (Kraft-q55aw)."""
    with pytest.raises(ValidationError, match="max_attempts"):
        policy.TaskPolicyOverride.model_validate({"max_attempts": 2})
    with pytest.raises(ValidationError, match="timeout_minutes"):
        policy.TaskPolicyOverride.model_validate({"timeout_minutes": 5})
    assert policy.TemplatePolicyOverride.model_validate({"max_attempts": 2}).max_attempts == 2


def test_the_meet_of_repository_layers_is_the_tightest_of_each_field():
    """Kraft-jc39p: a workspace item's assembled checkout holds every selected
    repository at once, so a task running there is bound by all of their
    layers -- each field at its tightest, never a relaxation of any one
    (`repository-policy-cannot-relax-instance-safety`)."""
    meet = policy.TemplatePolicyOverride.meet(
        [
            policy.TemplatePolicyOverride(
                allowed_tools=["Read", "Bash", "Edit"], token_budget=900, max_attempts=3
            ),
            policy.TemplatePolicyOverride(
                allowed_tools=["Read", "Bash"], deny_tools=["WebFetch"], allowed_harnesses=["a"]
            ),
            policy.TemplatePolicyOverride(
                token_budget=500, deny_tools=["Bash"], sandbox=_SANDBOX, timeout_minutes=9
            ),
        ]
    )
    assert meet.allowed_tools == ["Read", "Bash"]
    assert meet.deny_tools == ["WebFetch", "Bash"]
    assert (meet.token_budget, meet.max_attempts, meet.timeout_minutes) == (500, 3, 9)
    assert meet.allowed_harnesses == ["a"]
    assert meet.sandbox == policy.SandboxPolicy(**_SANDBOX)
    assert policy.TemplatePolicyOverride.meet([]) == policy.TemplatePolicyOverride()


def test_repository_layers_with_two_different_sandboxes_have_no_meet():
    """No process can run in two containers; which one wins would be a guess."""
    other = {**_SANDBOX, "image": "other"}
    with pytest.raises(policy.PolicyError, match="sandbox") as refused:
        policy.TemplatePolicyOverride.meet(
            [
                policy.TemplatePolicyOverride(sandbox=_SANDBOX),
                policy.TemplatePolicyOverride(sandbox=other),
            ]
        )
    assert refused.value.field == "sandbox"


def test_the_docsite_policy_example_leaves_allowed_tools_unset(tmp_path):
    """Review E #2 (Kraft-6pbq4): a `maxima.allowed_tools` binds every task,
    and codex cannot enforce a tool list, so it refuses to launch -- copying the
    docs' example stopped every `codex` task on the shipped chain."""
    import re

    page = (Path(__file__).resolve().parents[1] / "docsite" / "configuration.md").read_text()
    block = re.search(r"## `policy.yaml`.*?```yaml\n(.*?)```", page, re.DOTALL).group(1)
    path = tmp_path / "policy.yaml"
    path.write_text(block)
    instance = policy.PolicyInput.from_yaml(path).instance_policy()
    assert instance.allowed_tools is None and instance.maxima.allowed_tools is None
