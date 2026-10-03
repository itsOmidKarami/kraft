"""What `kraft admin doctor` says about the 2.0 moves: the `moved keys` row
(config keys moved or renamed, still read under their old names, named where
each lives now) and the `config` row's warning about a `templates/` left
beside `config/`."""

from __future__ import annotations

import asyncio

import pytest

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
    assert "the next start moves them" in check["detail"]

    # A key 1.4 ignored and 2.0's schedule refuses keeps them there: say so (R12c-02).
    (live / "policy.yaml").write_text(
        "default: {attempts: 1, wall_clock_s: 1}\ntriggers:\n"
        "  - {cron: '0 9 * * *', repo: /r, chain: default, title: t, enabled: false}\n"
    )
    check = next(r for r in asyncio.run(doctor.run_checks()) if r["name"] == "moved keys")
    assert "they stay because intake.yaml's schedules refuse triggers.0.enabled" in check["detail"]

    (live / "intake.yaml").write_text("enabled: false\n")
    (live / "policy.yaml").write_text("default: {attempts: 1, wall_clock_s: 1}\n")
    (live / "repos.yaml").write_text("repos: []\n")
    (live / "theme.yaml").write_text("board: {group_by: chain}\n")
    check = next(r for r in asyncio.run(doctor.run_checks()) if r["name"] == "moved keys")
    assert (check["ok"], check.get("warn", False)) == (True, False)


@pytest.mark.parametrize("templates", ["a-directory-of-its-own", "the-link-the-rename-leaves"])
def test_doctor_warns_when_a_1x_templates_directory_sits_beside_config(
    tmp_path, monkeypatch, templates
):
    """A `config/` made by hand before the first 2.0 start, beside a 1.x home
    whose clash the rename left: one of the two is unread. The link the
    rename leaves is no such thing."""
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path))
    monkeypatch.setenv("KRAFT_CONFIG_DIR", "")
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", "")
    (tmp_path / "config" / "chains").mkdir(parents=True)
    if templates == "a-directory-of-its-own":
        (tmp_path / "templates" / "harnesses").mkdir(parents=True)
    else:
        (tmp_path / "templates").symlink_to("config", target_is_directory=True)

    check = next(r for r in asyncio.run(doctor.run_checks()) if r["name"] == "config")
    warned = templates == "a-directory-of-its-own"
    assert (check["ok"], check.get("warn", False)) == (True, warned), check["detail"]
    assert ("both exist" in check["detail"]) == warned
