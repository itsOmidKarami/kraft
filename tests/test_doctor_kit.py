"""`kraft doctor` on a `kind: kit` repository: the Kit fetched and lowered
as the walk would, the sandbox rows run on what it lowers to, and a warning
for what the Kit leaves out (spec §9.5)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from support.harness import make_repo

from kraft import client, doctor
from kraft.worker import kit

# `app` fixture: tests/conftest.py. It wires client.transport.http() to the ASGI app.

FIXTURES = Path(__file__).parent / "fixtures" / "kit" / "m7"
DESCRIPTOR = (FIXTURES / "kraft" / "egress-credential-resources.yaml").read_text()
REF = "registry.example.com/acme/kraft-worker@sha256:" + "a" * 64
OPTIONAL = {
    "type": kit.CREDENTIAL,
    "optional": True,
    "config": {
        "service": "sentry",
        "phase": "runtime",
        "apiKey": {
            "name": "SENTRY_TOKEN",
            "proxyManaged": True,
            "inject": [{"domain": "api.anthropic.com", "header": "x-sentry"}],
        },
    },
}


def _rows(app, tmp_path, monkeypatch, descriptor: str, fetch=None) -> list[dict]:
    """Doctor's rows for one repository sandboxed by `REF`, answering
    `descriptor`, its `anthropic` credential bound to a daemon variable."""
    monkeypatch.setattr(
        "kraft.worker.backends.docker.DockerBackend.health", AsyncMock(return_value=(True, "ok"))
    )
    monkeypatch.setenv("KRAFT_ANTHROPIC_KEY", "sk")

    async def answer(ref):
        return kit.Fetched(ref, ref.rpartition("@")[2], descriptor)

    monkeypatch.setattr(kit, "fetch", fetch or answer)
    templates = tmp_path / "templates"
    (templates / "sandbox.yaml").write_text(
        yaml.safe_dump({"credentials": {"anthropic": "KRAFT_ANTHROPIC_KEY"}})
    )
    asyncio.run(client.ensure_repo(str(make_repo(tmp_path))))
    data = yaml.safe_load((templates / "repos.yaml").read_text())
    data["repos"][0]["sandbox"] = {"kind": "kit", "runtime": "docker", "kit": REF}
    (templates / "repos.yaml").write_text(yaml.safe_dump(data))
    return [
        r
        for r in asyncio.run(doctor.run_checks())
        if r["name"].split(" ")[0] in ("sandbox", "credentials", "egress", "kit")
        and r["name"] != "egress listener"
    ]


def _with(*capabilities: dict) -> str:
    descriptor = yaml.safe_load(DESCRIPTOR)
    descriptor["capabilities"] += capabilities
    return json.dumps(descriptor)


async def _unreachable(ref):
    raise kit.KitRefused(f"`manifest inspect {ref}` failed: unauthorized")


@pytest.mark.parametrize(
    ("descriptor", "fetch", "said"),
    [
        (DESCRIPTOR, None, None),
        (_with({"type": "com.docker.sandbox/lifecycle@1", "config": {}}), None, "lifecycle@1"),
        (DESCRIPTOR, _unreachable, "unauthorized"),
    ],
    ids=["ok", "refused-type", "fetch-failed"],
)
def test_doctor_reads_a_kit_and_checks_what_it_lowers_to(
    app, tmp_path, monkeypatch, descriptor, fetch, said
):
    """Its credentials row reads the bound variable, as a launch does; a Kit
    that cannot be used fails the sandbox row naming it and why."""
    rows = _rows(app, tmp_path, monkeypatch, descriptor, fetch)

    by_kind = {r["name"].split(" ")[0]: r for r in rows}
    if said is None:
        assert (by_kind["sandbox"]["ok"], by_kind["credentials"]["ok"]) == (True, True)
        assert (
            "ANTHROPIC_API_KEY (runtime only) on api.anthropic.com"
            in (by_kind["credentials"]["detail"])
        )
        assert "egress" not in by_kind
        return
    assert [r["name"].split(" ")[0] for r in rows] == ["sandbox"]
    assert not by_kind["sandbox"]["ok"]
    assert REF in by_kind["sandbox"]["detail"]
    assert said in by_kind["sandbox"]["detail"]


def test_doctor_warns_of_each_harness_host_the_kit_does_not_allow(app, tmp_path, monkeypatch):
    """The claude worker Kit allows claude's hosts and no other harness's."""
    [row] = [
        r
        for r in _rows(app, tmp_path, monkeypatch, DESCRIPTOR)
        if r["name"].startswith("kit hosts")
    ]

    assert (row["ok"], row["warn"]) == (True, True)
    assert "codex requires api.openai.com, chatgpt.com" in row["detail"]
    assert "claude" not in row["detail"]


def test_doctor_warns_of_a_credential_service_with_no_binding(app, tmp_path, monkeypatch):
    rows = _rows(app, tmp_path, monkeypatch, _with(OPTIONAL))

    [row] = [r for r in rows if r["name"].startswith("kit credentials")]
    assert (row["ok"], row["warn"]) == (True, True)
    assert "no binding for sentry" in row["detail"]
