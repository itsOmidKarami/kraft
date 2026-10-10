"""`storage.worktrees` in policy.yaml: sizes, and the quota beside the limit."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kraft import policy

_BASE = {"default": {"attempts": 3, "wall_clock_s": 3600}}


def _policy(worktrees: dict | None) -> policy.Policy:
    data = _BASE if worktrees is None else {**_BASE, "storage": {"worktrees": worktrees}}
    return policy.Policy.from_input(policy.PolicyInput.model_validate(data), source="policy.yaml")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("512K", 512 * 1024),
        ("1000M", 1000 * 1024**2),
        ("10g", 10 * 1024**3),
        ("1T", 1024**4),
        ("10GB", 10 * 1024**3),
    ],
    ids=["K", "M", "lower-g", "T", "trailing-B"],
)
def test_size_bytes_reads_a_whole_number_with_a_unit(text, expected):
    assert policy.size_bytes(text) == expected


@pytest.mark.parametrize(
    "text",
    [10, "10", "1.5G", "0G", "G", "-1G"],
    ids=["bare-int", "bare-str", "fraction", "zero", "no-number", "negative"],
)
def test_size_bytes_refuses_anything_else_and_names_the_fix(text):
    with pytest.raises(ValueError, match="10G"):
        policy.size_bytes(text)


@pytest.mark.parametrize(
    ("worktrees", "attr", "expected"),
    [
        ({"limit": "10G"}, "storage_limit_bytes", 10 * 1024**3),
        ({"limit": "10G"}, "storage_quota_bytes", 8 * 1024**3),
        ({"limit": "10G", "quota": "5G"}, "storage_quota_bytes", 5 * 1024**3),
        (None, "storage_limit_bytes", None),
        (None, "storage_quota_bytes", None),
        ({"limit": "10G"}, "storage_auto_cleanup_min_age_s", None),
        ({"limit": "10G", "auto_cleanup": None}, "storage_auto_cleanup_min_age_s", None),
        ({"limit": "10G", "auto_cleanup": {}}, "storage_auto_cleanup_min_age_s", 24 * 3600),
        (
            {"limit": "10G", "auto_cleanup": {"min_age": "2D"}},
            "storage_auto_cleanup_min_age_s",
            2 * 86400,
        ),
        ({"limit": "10G", "auto_cleanup": {"min_age": "0h"}}, "storage_auto_cleanup_min_age_s", 0),
    ],
    ids=[
        "limit",
        "quota-defaults-to-80-percent",
        "quota-as-written",
        "no-limit",
        "no-quota",
        "auto-cleanup-off-by-default",
        "auto-cleanup-null",
        "auto-cleanup-default-24h",
        "auto-cleanup-days",
        "auto-cleanup-no-floor",
    ],
)
def test_storage_worktrees_loads(worktrees, attr, expected):
    assert getattr(_policy(worktrees), attr) == expected


@pytest.mark.parametrize(
    ("worktrees", "message"),
    [
        ({"limit": "10G", "quota": "10G"}, "must be below limit"),
        ({"limit": "10G", "quota": "11G"}, "must be below limit"),
        ({"quota": "8G"}, "needs a limit"),
        ({"limit": 10}, "10G"),
        ({"limit": "10G", "cap": "1G"}, "cap"),
        ({"auto_cleanup": {}}, "needs a limit"),
        ({"limit": "10G", "auto_cleanup": {"min_age": 24}}, "24h"),
        ({"limit": "10G", "auto_cleanup": {"min_age": "1w"}}, "24h"),
    ],
    ids=[
        "quota-equals-limit",
        "quota-over-limit",
        "quota-alone",
        "bare-int",
        "unknown-key",
        "auto-cleanup-without-limit",
        "auto-cleanup-age-without-unit",
        "auto-cleanup-weeks",
    ],
)
def test_storage_worktrees_refuses(worktrees, message):
    with pytest.raises(ValidationError, match=message):
        policy.PolicyInput.model_validate({**_BASE, "storage": {"worktrees": worktrees}})


@pytest.mark.parametrize(
    "text",
    [24, "24", "1w", "90m", "1.5d", "-1h", "h"],
    ids=["bare-int", "bare-str", "weeks", "minutes", "fraction", "negative", "no-number"],
)
def test_age_seconds_refuses_anything_but_hours_and_days_and_names_the_fix(text):
    with pytest.raises(ValueError, match="24h"):
        policy.age_seconds(text)
