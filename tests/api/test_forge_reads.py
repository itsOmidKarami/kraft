"""The item-scoped forge read routes: what a worker may read of its item's
merge request and CI without `gh`, `glab` or a token."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

import pytest
from support.api import _on_api_account
from support.harness import make_repo

from kraft import store
from kraft.adapters.forge import FakeForge, ForgeError, Job, Label, MergedMR, Pipeline

_PIPE = Pipeline("p1", "branch", "b", "abc", "success", "http://f/p1", "2026-10-30T00:00:00Z")
_JOB = Job("j1", "lint", "test", "failed", "script_failure", "http://f/j1")
_MERGED = MergedMR(7, "older", ("bug",), "http://f/7", "2026-10-29T00:00:00Z")
_LABEL = Label("bug", "a defect", "d73a4a")


@dataclass
class Spy(FakeForge):
    """A `FakeForge` that records the reads it was asked for, with `repo`."""

    calls: list = field(default_factory=list)
    fail: ForgeError | None = None

    async def pipelines(self, *, repo, branch, base):
        self.calls.append(("pipelines", repo, base))
        if self.fail:
            raise self.fail
        return await super().pipelines(repo=repo, branch=branch, base=base)

    async def jobs(self, *, repo, pipeline_id):
        self.calls.append(("jobs", pipeline_id))
        return await super().jobs(repo=repo, pipeline_id=pipeline_id)

    async def job_log(self, *, repo, job_id):
        self.calls.append(("job_log", job_id))
        return await super().job_log(repo=repo, job_id=job_id)


def _spy(**kwargs) -> Spy:
    return Spy(
        pipelines_seen=(_PIPE,),
        jobs_seen={"p1": (_JOB,)},
        logs_seen={"j1": "ok\n"},
        merged_seen=(_MERGED,),
        labels_seen=(_LABEL,),
        **kwargs,
    )


def _item(client, repo, monkeypatch, fake) -> str:
    monkeypatch.setattr("kraft.adapters.forge.run.backend_for", lambda *a, **k: "fake")
    monkeypatch.setattr("kraft.adapters.forge.run.resolve", lambda name: fake)
    return client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]


def _branch(client, wid) -> str:
    row = client.portal.call(
        client.app.state.db.read,
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone(),
    )
    return store.branch_for(row)


def _with_mr(client, wid, fake) -> None:
    branch = _branch(client, wid)

    async def open_it():
        await fake.open_mr(
            repo=client.app.state.run_dirs.worktrees / wid,
            branch=branch,
            base="main",
            title="t",
            body="b",
        )

    client.portal.call(open_it)


@pytest.mark.parametrize(
    ("path", "want"),
    [
        ("mr", None),
        ("pipelines", [asdict(_PIPE)]),
        ("pipelines/p1/jobs", [asdict(_JOB)]),
        ("pipelines/p1/jobs/j1/log", {"log": "ok", "truncated": False}),
        ("merged", [asdict(_MERGED)]),
        ("labels", [asdict(_LABEL)]),
    ],
    ids=["mr", "pipelines", "jobs", "job-log", "merged", "labels"],
)
def test_each_forge_read_answers_what_the_forge_holds(client, repo, monkeypatch, path, want):
    fake = _spy()
    wid = _item(client, repo, monkeypatch, fake)
    _with_mr(client, wid, fake)
    body = client.get(f"/api/work-items/{wid}/forge/{path}").json()
    if want is None:
        assert (body["number"], body["title"], body["state"]) == (1, "t", "open")
    else:
        assert body == json.loads(json.dumps(want))


@pytest.mark.parametrize(
    "path",
    ["pipelines/foreign/jobs", "pipelines/foreign/jobs/j1/log", "pipelines/p1/jobs/foreign/log"],
    ids=["foreign-pipeline-jobs", "foreign-pipeline-log", "foreign-job-in-own-pipeline"],
)
def test_a_pipeline_or_job_not_the_items_is_404(client, repo, monkeypatch, path):
    fake = _spy()
    wid = _item(client, repo, monkeypatch, fake)
    assert client.get(f"/api/work-items/{wid}/forge/{path}").status_code == 404
    assert ("job_log", "j1") not in fake.calls and ("job_log", "foreign") not in fake.calls
    assert ("jobs", "foreign") not in fake.calls


def test_an_item_with_no_mr_yet_still_reads_its_branch_and_repo(client, repo, monkeypatch):
    wid = _item(client, repo, monkeypatch, _spy())
    gone = client.get(f"/api/work-items/{wid}/forge/mr")
    assert gone.status_code == 404
    assert _branch(client, wid) in gone.json()["detail"]
    for path in ("pipelines", "merged", "labels"):
        assert client.get(f"/api/work-items/{wid}/forge/{path}").status_code == 200


@pytest.mark.parametrize(
    ("error", "status", "headers"),
    [
        (ForgeError("slow", reason="rate_limited", retry_after=30), 429, {"retry-after": "30"}),
        (ForgeError("gone", reason="not_found"), 404, {}),
        (ForgeError("boom"), 502, {}),
    ],
    ids=["rate-limited-429-with-retry-after", "not-found-404", "other-502"],
)
def test_a_forge_error_maps_to_a_status(client, repo, monkeypatch, error, status, headers):
    wid = _item(client, repo, monkeypatch, _spy(fail=error))
    got = client.get(f"/api/work-items/{wid}/forge/pipelines")
    assert got.status_code == status
    assert {k: got.headers[k] for k in headers} == headers


def test_a_member_is_read_with_its_own_forge_checkout_and_base(client, repo, monkeypatch, tmp_path):
    root_fake, member_fake = _spy(), _spy()
    wid = _item(client, repo, monkeypatch, root_fake)
    member = make_repo(tmp_path, name="core")
    client.portal.call(
        client.app.state.db.write,
        lambda c: store.repos.add_repo(
            c,
            work_item_id=wid,
            repo_path=str(member),
            role="submodule",
            merge_rank=0,
            submodule_path="libs/core",
        ),
    )
    monkeypatch.setattr(
        "kraft.adapters.forge.run._member_forges", lambda *a, **k: {"libs/core": member_fake}
    )
    assert client.get(f"/api/work-items/{wid}/forge/pipelines?repo=libs/core").status_code == 200
    assert client.get(f"/api/work-items/{wid}/forge/pipelines").status_code == 200
    other = client.get(f"/api/work-items/{wid}/forge/pipelines?repo=libs/other")
    assert (other.status_code, "libs/core" in other.json()["detail"]) == (404, True)
    assert member_fake.calls == [("pipelines", member, "main")]
    assert [c[0] for c in root_fake.calls] == ["pipelines"]
    assert root_fake.calls[0][1] != member


@pytest.mark.parametrize(
    "mount",
    ["..", "/etc", "libs/other", "libs/core"],
    ids=["dotdot", "absolute", "unknown-mount", "single-repo-item"],
)
def test_a_repo_that_is_not_a_member_is_404(client, repo, monkeypatch, mount):
    fake = _spy()
    wid = _item(client, repo, monkeypatch, fake)
    # `pipelines`, which the spy records, so `calls == []` shows no forge read ran.
    got = client.get(f"/api/work-items/{wid}/forge/pipelines", params={"repo": mount})
    assert got.status_code == 404
    assert "none" in got.json()["detail"]
    assert fake.calls == []


def test_the_job_log_is_its_tail_without_ansi(client, repo, monkeypatch):
    fake = _spy()
    fake.logs_seen["j1"] = "".join(f"\x1b[31mline {i}\x1b[0m\n" for i in range(10_000))
    wid = _item(client, repo, monkeypatch, fake)
    url = f"/api/work-items/{wid}/forge/pipelines/p1/jobs/j1/log"
    body = client.get(url, params={"lines": 200}).json()
    lines = body["log"].split("\n")
    assert (len(lines), lines[-1], body["truncated"]) == (200, "line 9999", True)
    assert "\x1b" not in body["log"]
    assert [client.get(url, params={"lines": n}).status_code for n in (0, 5001)] == [422, 422]


@pytest.mark.parametrize(
    "log, lines, expected",
    [
        pytest.param(
            "section_start:1:build\r\x1b[0KBuilding\n10%\r50%\r100%\nsection_end:1:build\r\x1b[0K\n",
            3,
            {"log": "Building\n100%\n", "truncated": False},
            id="gitlab-sections-and-a-progress-bar",
        ),
        pytest.param("a\r\nb\r\n", 1, {"log": "b", "truncated": True}, id="crlf"),
        pytest.param("", 5, {"log": "", "truncated": False}, id="empty"),
        pytest.param(
            "a\r\r\nb\r", 5, {"log": "a\nb", "truncated": False}, id="double-cr-and-trailing-cr"
        ),
    ],
)
def test_the_job_log_keeps_what_a_carriage_return_left(
    client, repo, monkeypatch, log, lines, expected
):
    fake = _spy()
    fake.logs_seen["j1"] = log
    wid = _item(client, repo, monkeypatch, fake)
    url = f"/api/work-items/{wid}/forge/pipelines/p1/jobs/j1/log"
    assert client.get(url, params={"lines": lines}).json() == expected


def _add_member(client, wid, repo, mount) -> None:
    client.portal.call(
        client.app.state.db.write,
        lambda c: store.repos.add_repo(
            c,
            work_item_id=wid,
            repo_path=str(repo),
            role="submodule",
            merge_rank=0,
            submodule_path=mount,
        ),
    )


def test_an_api_account_item_reads_nothing_through_the_cli_when_forges_yaml_is_broken(
    client, repo, monkeypatch
):
    # the broken file may hold the API host default: reading it as no accounts is the switch
    wid = _item(client, repo, monkeypatch, _spy())
    _on_api_account(client, repo, monkeypatch, wid, pinned=False)
    monkeypatch.setattr(client.app.state, "forges", None)
    monkeypatch.setattr(client.app.state, "invalid_forges", "bad yaml", raising=False)
    got = client.get(f"/api/work-items/{wid}/forge/pipelines")
    assert got.status_code == 502
    assert "forges.yaml does not load (bad yaml)" in got.json()["detail"]


@pytest.mark.parametrize(
    "forge, mount, pinned",
    [
        ("gitlab", None, True),
        ("gitlab", "libs/core", True),
        ("github", None, False),
        ("github", None, True),
        ("github", "libs/core", False),
    ],
    ids=[
        "gitlab-root",
        "gitlab-member",
        "github-before-any-pin",
        "github-pinned",
        "github-member-with-no-forge-of-its-own",
    ],
)
def test_an_api_account_item_reads_through_its_account(
    client, repo, monkeypatch, forge, mount, pinned
):
    fake, built = _spy(), []
    wid = _item(client, repo, monkeypatch, fake)
    _on_api_account(client, repo, monkeypatch, wid, pinned=pinned, forge=forge)
    monkeypatch.setattr(
        "kraft.adapters.forge.run.api_forge", lambda name, account: built.append(name) or fake
    )
    if mount:
        _add_member(client, wid, repo, mount)
    got = client.get(
        f"/api/work-items/{wid}/forge/pipelines", params={"repo": mount} if mount else None
    )
    assert got.status_code == 200, got.text
    assert built == [{"github": "corp-gh", "gitlab": "corp-gl"}[forge]]
    assert fake.calls[0][0] == "pipelines"
