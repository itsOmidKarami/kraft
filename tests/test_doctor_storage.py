"""What `kraft admin doctor` says about worktree storage."""

from __future__ import annotations

import pytest

from kraft import doctor


@pytest.mark.parametrize(
    ("state", "used", "ok", "warn", "says"),
    [
        ("ok", 70, True, False, "of 100K"),
        ("over_quota", 90, True, True, "over the 80K quota"),
        ("over_quota", 120, True, True, "over the limit in items' documents"),
        ("held", 120, False, False, "starts that need a new worktree are held"),
    ],
    ids=["ok", "over-quota", "over-limit-in-documents", "held"],
)
def test_doctor_says_where_storage_stands(state, used, ok, warn, says):
    payload = {
        "status": "degraded" if state == "held" else "ok",
        "storage": {
            "state": state,
            "used_bytes": used * 1024,
            "quota_bytes": 80 * 1024,
            "limit_bytes": 100 * 1024,
        },
    }
    line = next(c for c in doctor._health_checks(payload) if says in c["detail"])
    assert (line["ok"], line["warn"]) == (ok, warn)
    assert "kraft view storage" in line["detail"]


def test_doctor_says_how_many_items_were_too_recent_to_clean_up():
    payload = {
        "status": "degraded",
        "storage": {
            "state": "held",
            "used_bytes": 120 * 1024,
            "quota_bytes": 80 * 1024,
            "limit_bytes": 100 * 1024,
            "too_recent": 2,
        },
    }
    line = next(c for c in doctor._health_checks(payload) if "held" in c["detail"])
    assert "2 finished items ended too recently for automatic clean-up" in line["detail"]
