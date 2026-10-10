"""What `kraft admin doctor` says about worktree storage."""

from __future__ import annotations

import pytest

from kraft import doctor


@pytest.mark.parametrize(
    ("state", "ok", "warn", "says"),
    [
        ("ok", True, False, "of 100K"),
        ("over_quota", True, True, "over the 80K quota"),
        ("held", False, False, "starts that need a new worktree are held"),
    ],
    ids=["ok", "over-quota", "held"],
)
def test_doctor_says_where_storage_stands(state, ok, warn, says):
    payload = {
        "status": "degraded" if state == "held" else "ok",
        "storage": {
            "state": state,
            "used_bytes": 90 * 1024,
            "quota_bytes": 80 * 1024,
            "limit_bytes": 100 * 1024,
        },
    }
    line = next(c for c in doctor._health_checks(payload) if says in c["detail"])
    assert (line["ok"], line["warn"]) == (ok, warn)
    assert "kraft view storage" in line["detail"]
