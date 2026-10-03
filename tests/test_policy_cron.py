"""A schedule's cron: what the scheduler runs (`policy.cron_due`) and what
every reader refuses (`policy._cron_fields`, `config.Schedule`)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from kraft import config, policy

#: Thursday 10 September 2026, 14:30 UTC, and the Sunday after.
_THURSDAY = datetime(2026, 9, 10, 14, 30, tzinfo=UTC)
_SUNDAY = datetime(2026, 9, 13, 14, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("cron", "due", "when"),
    [
        pytest.param("*/15 * * * *", True, _THURSDAY, id="step-hits"),
        pytest.param("*/7 * * * *", False, _THURSDAY, id="step-misses"),
        pytest.param("0 9 * * 1-5", False, _THURSDAY, id="weekday-range-wrong-hour"),
        pytest.param("30 14 * * 1-5", True, _THURSDAY, id="weekday-range"),
        pytest.param("30 14 * * 0,6", False, _THURSDAY, id="weekend-list"),
        pytest.param("30 14 * * 4", True, _THURSDAY, id="one-weekday"),
        pytest.param("30 14 * * 7", False, _THURSDAY, id="seven-is-not-thursday"),
        pytest.param("30 14 * * 7", True, _SUNDAY, id="seven-is-sunday"),
        pytest.param("30 14 * * 0", True, _SUNDAY, id="zero-is-sunday"),
        pytest.param("20-40/10 14 * * *", True, _THURSDAY, id="range-with-step"),
        pytest.param("20-40/20 14 * * *", False, _THURSDAY, id="range-with-step-misses"),
        pytest.param("10/20 14 * * *", True, _THURSDAY, id="start-with-step"),
        pytest.param("30 14 1-9 * *", False, _THURSDAY, id="day-of-month-range-misses"),
        pytest.param(
            "30 14 10 9 1", False, _THURSDAY, id="day-of-month-and-weekday-both-must-match"
        ),
        pytest.param("30 9-17 * 9-12 *", True, _THURSDAY, id="hour-and-month-ranges"),
    ],
)
def test_cron_due_reads_ranges_and_steps(cron, due, when):
    """Settings › Auto-intake describes `*/N` and `1-5` (R12a-01), so the
    scheduler runs them, as cron does."""
    assert policy.cron_due(cron, when) is due


@pytest.mark.parametrize(
    ("cron", "message"),
    [
        pytest.param("61 9 * * *", "field '61': minute 61 is outside 0-59", id="minute-61"),
        pytest.param("0 24 * * *", "hour 24 is outside 0-23", id="hour-24"),
        pytest.param("0 9 0 * *", "day of month 0 is outside 1-31", id="day-0"),
        pytest.param("0 9 * 13 *", "month 13 is outside 1-12", id="month-13"),
        pytest.param("0 9 * * 1-8", "day of week 8 is outside 0-7", id="weekday-8"),
        pytest.param("0 9 * * 5-1", "the range 5-1 runs backwards", id="backwards"),
        pytest.param("*/0 * * * *", "a step must be a positive integer", id="zero-step"),
        pytest.param("0 9 * * MON", "must be '*', an integer, a range", id="names"),
        pytest.param("every monday", "must have exactly 5 fields", id="words"),
        pytest.param("\u00b2 * * * *", "must be '*', an integer", id="a-superscript-digit"),
        pytest.param("*/\u0663 * * * *", "a step must be a positive integer", id="an-arabic-digit"),
    ],
)
def test_a_cron_the_scheduler_cannot_run_is_refused(cron, message):
    """`61 9 * * *` saved and never fired, and a refused shape stopped every
    schedule after it (R12a-01): the one check refuses both, in the
    scheduler and where `intake.yaml` is read. A digit is ASCII: `²` passed
    `isdigit` and then crashed `int`."""
    with pytest.raises(policy.PolicyError, match=re.escape(message)):
        policy._cron_fields("schedules[0].cron", cron)
    with pytest.raises(policy.PolicyError, match=re.escape(message)):
        policy.cron_due(cron, _THURSDAY)
    entry = {"cron": cron, "repo": "/r", "chain": "default", "title": "t"}
    with pytest.raises(ValidationError) as exc:
        config.Intake.model_validate({"schedules": [entry]})
    assert config.first_error(exc.value, "intake.yaml").startswith(
        "intake.yaml: 'schedules'.0.cron: "
    )
    assert message in str(exc.value)


@pytest.mark.parametrize(
    "cron",
    ["0 24 * * *", "60 * * * *", "0 9 * * 8", "0 9 0 * *"],
    ids=["hour-24", "minute-60", "weekday-8", "day-0"],
)
def test_a_1_4_trigger_out_of_range_is_skipped_not_the_whole_policy(tmp_path, caplog, cron):
    """1.4 checked only that a value was digits, so these loaded and never
    fired. Refusing them now refused the whole `policy.yaml`, and with it
    every intake door (R12 review P1-1): the trigger is skipped, warned of,
    and the rest of the file and its other triggers load."""
    path = tmp_path / "policy.yaml"
    path.write_text(
        "default: {attempts: 1, wall_clock_s: 1}\ntriggers:\n"
        f"  - {{cron: '{cron}', repo: /r, chain: c, title: never}}\n"
        "  - {cron: '0 9 * * 1', repo: /r, chain: c, title: weekly}\n"
    )
    with caplog.at_level("WARNING", logger="kraft.policy"):
        loaded = policy.load_policy(path)
    assert [t.title for t in loaded.triggers] == ["weekly"]
    assert any("triggers[0].cron" in r.message and "skipped" in r.message for r in caplog.records)
