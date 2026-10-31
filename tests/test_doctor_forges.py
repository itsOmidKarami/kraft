"""`kraft admin doctor`'s forges.yaml row."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from kraft import client, doctor
from kraft.paths import config_dir


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


def _api(probe, name="gl"):
    return {name: {"client": "api", "forge": "gitlab", "probe": probe}}


def _probe(**kw):
    return {
        "state": "ok", "detail": "signed in as ann", "login": "ann", "scopes": ["api"],
        "expires_on": None, "warnings": [], "sso_url": "", "projects": {}, **kw,
    }  # fmt: skip


_API_FILE = (
    "accounts:\n  gl:\n    forge: gitlab\n    client: api\n    web_url: https://gl.example\n"
)
_SOON = (date.today() + timedelta(days=3)).isoformat()


@pytest.mark.parametrize(
    ("accounts", "ok", "warn", "says"),
    [
        (_api(_probe(expires_on="2099-01-01")), True, False, "ann · scopes api · expires on 2099"),
        (_api(_probe(expires_on=_SOON)), True, True, f"expires on {_SOON} · renew it soon"),
        (
            _api(_probe(state="refused", detail="HTTP 401", sso_url="https://sso.example/x")),
            False, False, "refused: HTTP 401 · authorize it: https://sso.example/x",
        ),
        (_api(None), True, True, "not probed yet; kraft admin forge test gl"),
        (_api(_probe(warnings=["GH_TOKEN is read"])), True, True, "warning: GH_TOKEN is read"),
        ({"c": {"client": "cli", "forge": "gitlab", "probe": None}}, None, None, None),
        (ValueError("403"), True, False, "skipped: the server's forge accounts could not be read"),
        (ValueError("403"), None, None, None),
        (_api(_probe(state="unreachable", detail="no route")), True, True, "unreachable: no"),
    ],
    ids=[
        "ok", "expiring", "refused-with-sso", "no-probe-yet", "well-known-env-warns",
        "cli-account-has-no-row", "inside-a-session-skips",
        "inside-a-session-without-an-api-account-has-no-row", "unreachable-warns",
    ],
)  # fmt: skip
def test_doctor_reports_a_forge_account_row(app, monkeypatch, accounts, ok, warn, says):
    async def listed():
        if isinstance(accounts, Exception):
            raise accounts
        return {"accounts": accounts}

    monkeypatch.setattr(client, "list_forges", listed)
    if isinstance(accounts, Exception) and ok is not None:  # an API account is configured
        (config_dir() / "forges.yaml").write_text(_API_FILE)
    rows = [r for r in asyncio.run(doctor.run_checks()) if r["name"].startswith("forge account")]
    if ok is None:
        assert rows == []
        return
    (row,) = rows
    assert (row["ok"], row["warn"]) == (ok, warn), row
    assert says in row["detail"], row
