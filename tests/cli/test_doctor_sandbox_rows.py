"""`kraft admin doctor`'s per-repository sandbox rows (proxy, ca, egress)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
import yaml
from support.harness import make_repo

from kraft import client, doctor


@pytest.mark.parametrize(
    "proxy, warned", [("http://127.0.0.1:3128", True), ("http://proxy.corp:3128", False)]
)
def test_doctor_warns_that_a_loopback_proxy_cannot_reach_a_sandbox(
    app, tmp_path, monkeypatch, proxy, warned
):
    """The container's loopback is its own, so the proxy is not forwarded."""
    for name in ("HTTP_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    rows = _sandboxed_doctor_rows(app, tmp_path, "proxy ")
    assert [(r["ok"], r["warn"]) for r in rows] == ([(True, True)] if warned else [])
    assert not warned or ("`network:`" in rows[0]["detail"] and proxy in rows[0]["detail"])


@pytest.mark.parametrize("usable", [False, True], ids=["unusable", "usable"])
def test_doctor_warns_that_an_unusable_ssl_cert_file_is_ignored_by_sandboxes(
    app, tmp_path, monkeypatch, usable
):
    """It is skipped, not refused, so only doctor can say it went unused."""
    ca = tmp_path / "ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----\n" if usable else "")
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    rows = _sandboxed_doctor_rows(app, tmp_path, "ca ")
    assert [(r["ok"], r["warn"]) for r in rows] == ([] if usable else [(True, True)])
    assert usable or ("holds no PEM certificate" in rows[0]["detail"])


@pytest.mark.parametrize(
    ("fields", "ok", "phrase"),
    [
        ({}, False, "is refused"),
        ({"unrestricted_network": True}, True, "gates are not enforced"),
        ({"network": {"runtime": {"allow": ["a.io"]}}}, False, "did not answer"),
    ],
    ids=["refused", "unrestricted", "set"],
)
def test_doctor_reports_a_sandbox_with_no_network_policy(
    app, tmp_path, monkeypatch, fields, ok, phrase
):
    """No `network:` fails the row (a warning with the opt-out); under it the
    loopback-proxy warning goes and the TLS listener is checked instead."""
    ready = AsyncMock(return_value=(True, "ready"))
    monkeypatch.setattr("kraft.worker.backends.docker.DockerBackend.health", ready)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")
    got = _sandboxed_doctor_rows(app, tmp_path, ("egress ", "proxy "), **fields)
    proxy = [("proxy", True, True)] * ("network" not in fields)
    assert [(r["name"].split()[0], r["ok"], r["warn"]) for r in got] == [("egress", ok, ok), *proxy]
    assert phrase in got[0]["detail"]


def _sandboxed_doctor_rows(app, tmp_path, prefix, **sandbox) -> list[dict]:
    """Doctor's rows starting with `prefix`, for one repository sandboxed
    in `img` (plus `sandbox`'s fields)."""
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    repos_yaml = tmp_path / "templates" / "repos.yaml"
    data = yaml.safe_load(repos_yaml.read_text())
    data["repos"][0]["sandbox"] = {"kind": "docker", "image": "img", **sandbox}
    repos_yaml.write_text(yaml.safe_dump(data))  # `network: None` is no network

    return [r for r in asyncio.run(doctor.run_checks()) if r["name"].startswith(prefix)]
