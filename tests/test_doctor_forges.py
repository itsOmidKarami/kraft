"""`kraft admin doctor`'s forges.yaml row."""

from __future__ import annotations

import pytest

from kraft import doctor


@pytest.mark.parametrize(
    ("text", "ok", "says"),
    [
        (None, True, "0 account(s)"),
        ("accounts:\n  a: {forge: github, web_url: 'https://github.com'}\n", True, "1 account(s)"),
        ("accounts:\n  a: {forge: github, web_url: 'http://github.com'}\n", False, "forges.yaml"),
    ],
    ids=["absent", "one-account", "http"],
)
def test_doctor_reports_a_forges_yaml_row(templates_dir, monkeypatch, text, ok, says):
    monkeypatch.setenv("KRAFT_CONFIG_DIR", str(templates_dir))
    (templates_dir / "forges.yaml").unlink(missing_ok=True)
    if text is not None:
        (templates_dir / "forges.yaml").write_text(text)
    row = next(r for r in doctor._config_checks() if r["name"] == "forges.yaml")
    assert row["ok"] is ok, row
    assert says in row["detail"], row
