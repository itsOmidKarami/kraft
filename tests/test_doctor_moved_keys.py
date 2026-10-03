"""`kraft admin doctor`'s `moved keys` row: the config keys 2.0 moved or
renamed, still read under their old names, named where each lives now."""

from __future__ import annotations

import asyncio

from kraft import doctor


def test_doctor_names_each_key_2_0_moved_and_still_reads(tmp_path, monkeypatch):
    """`intake.yaml`'s `max_concurrent`, `policy.yaml`'s `triggers`, a repo's
    `default_chain_template` and the theme's `group_by: template` are read
    under their old names so an upgrade stops nothing, and the `moved keys`
    row warns naming where each lives now."""
    live = tmp_path / "templates"
    (live / "chains").mkdir(parents=True)
    (live / "intake.yaml").write_text("enabled: false\nmax_concurrent: 2\n")
    (live / "policy.yaml").write_text(
        "default: {attempts: 1, wall_clock_s: 1}\n"
        "triggers:\n  - {cron: '0 9 * * *', repo: /r, chain: default, title: t}\n"
    )
    (live / "repos.yaml").write_text("repos:\n  - {path: /r, default_chain_template: default}\n")
    (live / "theme.yaml").write_text("board: {group_by: template}\n")
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(live))

    check = next(r for r in asyncio.run(doctor.run_checks()) if r["name"] == "moved keys")
    assert check["ok"] is True and check["warn"] is True
    for old in ("max_concurrent", "triggers", "default_chain_template", "group_by: template"):
        assert old in check["detail"], check["detail"]

    (live / "intake.yaml").write_text("enabled: false\n")
    (live / "policy.yaml").write_text("default: {attempts: 1, wall_clock_s: 1}\n")
    (live / "repos.yaml").write_text("repos: []\n")
    (live / "theme.yaml").write_text("board: {group_by: chain}\n")
    check = next(r for r in asyncio.run(doctor.run_checks()) if r["name"] == "moved keys")
    assert (check["ok"], check.get("warn", False)) == (True, False)
