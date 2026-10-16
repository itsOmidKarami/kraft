"""GET /work-items/{wid}/artifact — what a human reads at a spec or plan gate.

The document is read off disk, not out of the index: a reviewer sitting at an
open gate must see the file the agent just wrote, whether or not the indexer
has caught up. A new one is in the item's `docs/`; an item that ran before
that wrote it in its worktree's `.engineering/`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from support.harness import connected_repo, fake_templates_dir, v1_named_chain

from kraft import api, item_paths
from kraft.api.routes import board

#: No default repo entry for an unconnected repo, as before this used the shared client.
pytestmark = pytest.mark.api_client(default_setup=False)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _templates(tmp_path: Path) -> Path:
    """fake_templates_dir, plus a V1 `spec-only` chain: the shipped spec author
    (on the fake agent, which honours its `produces: spec` contract) and the
    gate that is about its document.

    Was a legacy `spec-only.yaml` beside the legacy chains, which the V1 intake
    door cannot resolve -- POST /work-items answered 422, and every fixture
    below failed on `KeyError: 'id'` reading the item id out of the error."""
    d = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (d / "chains" / "spec-only.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "spec-only",
                "nodes": [
                    {
                        "id": "spec",
                        "kind": "exec",
                        "tasks": [{"id": "author", "extends": "spec_author"}],
                    },
                    {
                        "id": "spec_approval",
                        "kind": "gate",
                        "artifact": "spec",
                        "reject_to": "spec",
                    },
                ],
            }
        )
    )
    return d


@pytest.fixture
def templates_dir(tmp_path):
    return _templates(tmp_path)


@pytest.fixture(autouse=True)
def _no_skill_overlay(tmp_path, monkeypatch):
    """No operator overlay: the bundled method files are what the hook resolves."""
    monkeypatch.setenv("KRAFT_SKILLS_DIR", str(tmp_path / "no-skills"))


def _await_gate(client, wid, gate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get(f"/api/work-items/{wid}").json().get("pending_gate") == gate:
            return
        time.sleep(0.2)
    raise AssertionError(f"{wid} never reached {gate}")


@pytest.fixture
def item_at_spec_gate(client, tmp_path):
    repo = connected_repo(tmp_path)
    wid = client.post(
        "/api/work-items",
        json={
            "autostart": True,
            "repo": str(repo),
            "title": "add a flag",
            "chain_template": "spec-only",
        },
    ).json()["id"]
    _await_gate(client, wid, "spec_approval")
    return wid


@pytest.fixture
def worktree(client, item_at_spec_gate) -> Path:
    return Path(client.get(f"/api/work-items/{item_at_spec_gate}").json()["worktree_path"])


def _spec(client, wid: str) -> Path:
    """Where the fake agent wrote the spec, as its contract told it: the item's
    `docs/spec.md`, outside the worktree."""
    return item_paths.docs_dir(client.app.state.run_dirs, wid) / "spec.md"


def _write_artifact(client, wid: str, text: str, *, older: bool = False) -> Path:
    """The document a compliant agent would have written.

    Written by the test, overwriting whatever `fixtures/fake-claude.sh` already
    wrote for this hook (it has honoured the artifact contract since Task 8):
    these tests are about the endpoint's contract, not about the fake's own
    fixed content. `older`: where an item that ran before `docs/` wrote it,
    in its worktree, with no `docs/` copy to read first.
    """
    st = client.app.state
    path = _spec(client, wid)
    if older:
        path.unlink()
        path = item_paths.path_of(st.run_dirs, wid, item_paths.legacy_document_name(wid, "spec"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.mark.parametrize("older", [False, True], ids=["new-place", "older-worktree"])
def test_gate_artifact_names_the_file_the_hook_wrote(client, item_at_spec_gate, older):
    wid = item_at_spec_gate
    path = _write_artifact(client, wid, "---\ntitle: A spec\n---\n\nthe body\n", older=older)
    name = (item_paths.legacy_document_name if older else item_paths.document_name)(wid, "spec")

    body = client.get(f"/api/work-items/{wid}").json()
    assert body["gate_artifact"] == name

    art = client.get(f"/api/work-items/{wid}/artifact").json()
    assert (art["path"], art["absolute_path"]) == (name, str(path))
    assert art["title"] == "A spec"
    assert art["content"].strip() == "the body"
    assert art["truncated"] is False

    assert client.post(f"/api/work-items/{wid}/gates/spec_approval/approve").status_code == 200
    docs = client.get(f"/api/work-items/{wid}/documents").json()["documents"]
    assert [d["path"] for d in docs if d["source_kind"] == "artifact"] == [name]


@pytest.mark.parametrize("route", ["artifact", "artifacts/spec"], ids=["pending-gate", "produced"])
def test_gate_artifact_carries_its_absolute_path_and_opens_in_an_editor(
    client, item_at_spec_gate, monkeypatch, route
):
    wid = item_at_spec_gate
    path = _write_artifact(client, wid, "# A spec\n")
    art = client.get(f"/api/work-items/{wid}/{route}").json()
    assert art["absolute_path"] == str(path)

    launched = []
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: launched.append(argv) or object())
    r = client.post(f"/api/work-items/{wid}/{route}/open", json={"editor": "code"})
    assert r.status_code == 200, r.text
    assert launched == [["/usr/bin/code", str(path)]]


@pytest.mark.parametrize("route", ["artifact", "artifacts/spec"], ids=["pending-gate", "produced"])
@pytest.mark.parametrize("link", ["file", "parent-dir"])
def test_gate_artifact_open_refuses_what_the_read_refuses(
    client, item_at_spec_gate, worktree, monkeypatch, link, route
):
    """`GET /artifact` walks the path with no symlink followed, so a link
    inside the worktree is a 404 there; the editor launch refuses it too, not
    just a link out of the worktree (R14e-03). The document is in `docs/`, so
    that is where the link is planted."""
    wid = item_at_spec_gate
    path = _write_artifact(client, wid, "# A spec\n")
    if link == "file":
        real = worktree / "real.md"
        path.rename(real)
        path.symlink_to(real)
    else:
        moved = worktree / "moved"
        path.parent.rename(moved)
        path.parent.symlink_to(moved)
    launched = []
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: launched.append(argv) or object())

    assert client.get(f"/api/work-items/{wid}/{route}").status_code == 404
    r = client.post(f"/api/work-items/{wid}/{route}/open", json={"editor": "code"})
    assert r.status_code == 404, r.text
    missing = {
        "artifact": "this work item's gate has no artifact",
        "artifacts/spec": "this work item has no spec on disk",
    }
    assert r.json()["detail"] == missing[route]
    assert launched == []


@pytest.mark.api_client(default_setup=False, peer=("10.0.0.5", 54321))
@pytest.mark.parametrize("route", ["artifact", "artifacts/spec"], ids=["pending-gate", "produced"])
def test_gate_artifact_open_is_refused_for_a_non_loopback_client(client, monkeypatch, route):
    launched = []
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: launched.append(argv) or object())
    st = client.app.state
    monkeypatch.setattr(st, "access", {**st.access, "password_hash": "x"}, raising=False)
    client.headers["authorization"] = f"Bearer {st.mcp_token}"
    r = client.post(f"/api/work-items/unknown/{route}/open", json={"editor": "code"})
    # 403 before 404: a remote caller learns nothing about which items exist.
    assert r.status_code == 403, r.text
    assert launched == []


@pytest.mark.parametrize(
    ("older", "origin"),
    [(False, None), (True, "event_ingest")],
    ids=["new-place", "older-worktree"],
)
def test_a_tasks_produced_document_reads_off_the_worktree_then_from_the_index(
    client, item_at_spec_gate, worktree, older, origin
):
    """A task's Output is the document it produced, not the session note that says
    where it is: read off disk while it is there. Removing the worktree leaves
    a document in `docs/` where it is; an older one went with the worktree, and
    the copy the gate's approval indexed, under its old name, is read instead."""
    wid = item_at_spec_gate
    _write_artifact(client, wid, "---\ntitle: A spec\n---\n\nthe body\n", older=older)
    name = (item_paths.legacy_document_name if older else item_paths.document_name)(wid, "spec")
    art = client.get(f"/api/work-items/{wid}/artifacts/spec").json()
    assert (art["kind"], art["path"], art["title"]) == ("spec", name, "A spec")
    assert art["content"].strip() == "the body"

    assert client.post(f"/api/work-items/{wid}/gates/spec_approval/approve").status_code == 200
    shutil.rmtree(worktree)
    art = client.get(f"/api/work-items/{wid}/artifacts/spec").json()
    assert (art["path"], art["title"], art.get("origin")) == (name, "A spec", origin)
    assert art["content"].strip() == "the body"


@pytest.mark.parametrize(
    "kind",
    ["work_brief", "spec_approval", "..%2F..%2Fsecrets"],
    ids=["a-kind-the-chain-does-not-produce", "a-gate-id", "a-path"],
)
def test_a_produced_document_is_only_a_kind_the_chain_produces(
    client, item_at_spec_gate, worktree, kind
):
    """A file at the kind's path is not enough: the chain must name the kind."""
    wid = item_at_spec_gate
    for written in ("spec", "work_brief"):
        path = worktree / ".engineering" / f"{written}s" / f"{wid}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# A {written}\n")
    r = client.get(f"/api/work-items/{wid}/artifacts/{kind}")
    assert r.status_code == 404, r.text


def test_a_produced_document_nobody_wrote_or_indexed_is_a_404(client, item_at_spec_gate):
    _spec(client, item_at_spec_gate).unlink()
    r = client.get(f"/api/work-items/{item_at_spec_gate}/artifacts/spec")
    assert r.status_code == 404, r.text


def test_a_gate_whose_agent_wrote_nothing_reports_no_artifact(client, item_at_spec_gate):
    """An agent can report done without honouring the contract. The gate stays
    answerable; there is just nothing to read.

    `fixtures/fake-claude.sh` always honours the contract now, so "wrote
    nothing" is simulated by removing what it wrote rather than by picking a
    fake invocation that skips it.
    """
    _spec(client, item_at_spec_gate).unlink()
    assert client.get(f"/api/work-items/{item_at_spec_gate}").json()["gate_artifact"] is None
    # A UI polls the detail endpoint while it waits for the agent's session to
    # finish; this case must not write an event on every poll, only the ones
    # below where the file was there and something then went wrong reading it.
    client.get(f"/api/work-items/{item_at_spec_gate}/artifact")
    client.get(f"/api/work-items/{item_at_spec_gate}/artifact")
    assert client.get(f"/api/work-items/{item_at_spec_gate}/artifact").status_code == 404
    events = client.get(f"/api/work-items/{item_at_spec_gate}/events").json()
    assert not [e for e in events if e["type"] == "artifact_refused"]


@pytest.mark.parametrize(
    ("route", "refused"),
    [("artifact", []), ("artifacts/spec", ["escaped_containment"])],
    ids=["pending-gate", "produced"],
)
def test_a_symlink_out_of_the_worktree_is_a_404(
    client, item_at_spec_gate, tmp_path, caplog, route, refused
):
    """A link planted at the document's name in `docs/` is no document
    (`item_paths.document`): the pending gate has none to show. The produced
    route reads that name all the same, and refuses it as an escape."""
    outside = tmp_path / "secret.md"
    outside.write_text("not yours")
    path = _spec(client, item_at_spec_gate)
    path.unlink()  # fake-claude.sh already wrote a real file here; swap it out
    path.symlink_to(outside)

    with caplog.at_level("WARNING", logger="kraft.api"):
        resp = client.get(f"/api/work-items/{item_at_spec_gate}/{route}")
    assert resp.status_code == 404
    assert "not yours" not in resp.text
    # The answer is the absent file's, whichever route: the reason is for the timeline.
    assert "escaped_containment" not in resp.text
    assert client.get(f"/api/work-items/{item_at_spec_gate}").json()["gate_artifact"] is None
    # Pins the escape branch specifically, not merely "some 404 happened": a
    # regression that made the containment check a no-op would still 404 (the
    # read would fail some other way) without ever logging this.
    assert (item_at_spec_gate in caplog.text) is bool(refused)

    # Spec §4: the reason has to reach a reviewer with no access to this log,
    # so it belongs on the item's own timeline too, not only in caplog.
    events = client.get(f"/api/work-items/{item_at_spec_gate}/events").json()
    refusals = [e["payload"]["reason"] for e in events if e["type"] == "artifact_refused"]
    assert refusals == refused


def test_a_symlinked_directory_out_of_the_worktree_is_a_404(
    client, item_at_spec_gate, tmp_path, caplog
):
    """The escape doesn't have to be the artifact file itself — a symlinked
    ancestor directory resolves outside the run folder exactly the same way.
    The pending gate sees no document; the produced route refuses the read."""
    outside_dir = tmp_path / "outside-docs"
    outside_dir.mkdir()
    (outside_dir / "spec.md").write_text("not yours either")

    docs = _spec(client, item_at_spec_gate).parent
    shutil.rmtree(docs)  # fake-claude.sh already wrote a real tree here
    docs.symlink_to(outside_dir, target_is_directory=True)

    assert client.get(f"/api/work-items/{item_at_spec_gate}/artifact").status_code == 404
    with caplog.at_level("WARNING", logger="kraft.api"):
        resp = client.get(f"/api/work-items/{item_at_spec_gate}/artifacts/spec")
    assert resp.status_code == 404
    assert item_at_spec_gate in caplog.text


def test_open_no_symlinks_refuses_an_ancestor_directory_swapped_for_a_symlink(tmp_path):
    """The case plain `O_NOFOLLOW` on the resolved leaf path misses: swapping an
    *ancestor* directory for a symlink after `resolve()` ran. This is the exact
    scenario a round-1 fix (leaf-only `O_NOFOLLOW`) let through -- the walk
    below must refuse it at the `.engineering` component, not just the leaf.
    """
    from kraft.worker.worktree_read import open_no_symlinks as _open_no_symlinks

    root = tmp_path / "root"
    (root / ".engineering" / "specs").mkdir(parents=True)
    (root / ".engineering" / "specs" / "x.md").write_text("legitimate")

    outside = tmp_path / "outside"
    (outside / "specs").mkdir(parents=True)
    (outside / "specs" / "x.md").write_text("not yours")

    shutil.rmtree(root / ".engineering")
    (root / ".engineering").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        _open_no_symlinks(root, ".engineering/specs/x.md")


@pytest.mark.parametrize(
    ("rel", "reason"),
    [(".", "unreadable"), ("a\x00b", "absent")],
    ids=["a-dot-ref", "a-nul-byte-ref"],
)
def test_read_worktree_file_refuses_a_bad_ref_instead_of_raising(tmp_path, rel, reason):
    """Agent-supplied paths `read_worktree_file` must never raise on (spec §5: a
    stray exception here strands the session row). A `rel` of "." makes
    `Path(rel).parts` empty, so `open_no_symlinks`'s `parts[-1]` used to raise
    `IndexError`; a NUL byte makes `resolve()` raise `ValueError`, not
    `OSError`."""
    from kraft.worker.worktree_read import read_worktree_file

    assert read_worktree_file(tmp_path, rel, max_bytes=1024) == (None, reason)


@pytest.mark.parametrize("reader", ["read_worktree_file", "worktree_file_path", "open_no_symlinks"])
def test_a_fifo_planted_at_the_path_is_refused_not_waited_on(tmp_path, reader):
    """The worker owns its worktree and can swap the artifact for a FIFO after
    the route found a file there: a plain `open` would wait for a writer for
    ever, with the server's event loop behind it. Every reader refuses a
    non-regular file. The call runs on a thread, and a writer opened after a
    few seconds frees one that waits, so a missing guard fails here instead of
    hanging the run."""
    from kraft.worker import worktree_read

    os.mkfifo(tmp_path / "doc.md")
    outcome = []

    def call():
        if reader == "read_worktree_file":
            outcome.append(worktree_read.read_worktree_file(tmp_path, "doc.md", 1024))
        elif reader == "worktree_file_path":
            outcome.append(worktree_read.worktree_file_path(tmp_path, "doc.md"))
        else:
            try:
                os.close(worktree_read.open_no_symlinks(tmp_path, "doc.md"))
                outcome.append("opened")
            except OSError:
                outcome.append("refused")

    caller = threading.Thread(target=call, daemon=True)
    caller.start()
    caller.join(3)
    waited = caller.is_alive()
    if waited:
        with contextlib.suppress(OSError):
            os.close(os.open(tmp_path / "doc.md", os.O_WRONLY | os.O_NONBLOCK))
        caller.join(3)

    assert not waited, "the open waited for a writer"
    assert outcome == [
        {
            "read_worktree_file": (None, "unreadable"),
            "worktree_file_path": None,
            "open_no_symlinks": "refused",
        }[reader]
    ]


def test_artifact_over_the_cap_truncates_without_500ing_on_a_split_codepoint(
    client, item_at_spec_gate
):
    """Spec §7's named-but-uncovered branch: `fh.read(DIFF_MAX_BYTES + 1)`,
    `len(data) > DIFF_MAX_BYTES`, and `data[:DIFF_MAX_BYTES].decode(errors=
    "replace")`. The last of those only earns its keep if the cut can land mid-
    codepoint, so the file is built to put the two-byte lead byte of an "é"
    exactly at the cut point.
    """
    from kraft.api.routes.artifacts import DIFF_MAX_BYTES

    before = b"a" * (DIFF_MAX_BYTES - 1)  # cut lands right after this
    split_char = "é".encode()  # 2 bytes; the cut falls between them
    after = b"a" * 9
    content = before + split_char + after
    assert len(content) == DIFF_MAX_BYTES + 10

    _spec(client, item_at_spec_gate).write_bytes(content)

    resp = client.get(f"/api/work-items/{item_at_spec_gate}/artifact")
    assert resp.status_code == 200  # not a 500 from a decode error
    body = resp.json()
    assert body["truncated"] is True
    assert len(body["content"]) <= DIFF_MAX_BYTES


def _await_artifact_docs(client, wid: str) -> list:
    """The ingest runs after the approve response; wait for its document."""
    deadline = time.monotonic() + 10
    while True:
        docs = client.get(f"/api/work-items/{wid}/documents").json()["documents"]
        found = [d for d in docs if d["source_kind"] == "artifact"]
        if found or time.monotonic() > deadline:
            return found
        time.sleep(0.05)


def test_approving_a_gate_ingests_its_artifact_into_the_index(client, item_at_spec_gate):
    """A gate document is never committed (it is outside the worktree, or,
    for an older item, under `.engineering/`, which
    `forge._work_product_pathspec` excludes), so gate approval is what makes
    the spec durable:
    it must land in the index right after the approve response, not on the
    indexer's own scan schedule."""
    _write_artifact(client, item_at_spec_gate, "---\ntitle: A spec\n---\n\nthe body\n")

    resp = client.post(f"/api/work-items/{item_at_spec_gate}/gates/spec_approval/approve")
    assert resp.status_code == 200

    artifacts = _await_artifact_docs(client, item_at_spec_gate)
    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "specs"
    assert artifacts[0]["path"] == item_paths.document_name(item_at_spec_gate, "spec")
    # W13 A: an artifact has no session, so no run info.
    assert (artifacts[0]["attempt"], artifacts[0]["round"], artifacts[0]["session_status"]) == (
        None,
        None,
        None,
    )

    doc = client.get(f"/api/documents/{artifacts[0]['document_id']}").json()
    assert doc["title"] == "A spec"
    assert doc["content"] == "\nthe body\n"


def test_approving_a_gate_survives_a_broken_index(client, item_at_spec_gate, monkeypatch):
    """A locked index or an unwritable index.db must not block a gate the
    agent already satisfied. Same contract `ingest_session_summary` gives the
    event-drain loop (the index never blocks the executor) -- enforced here
    instead, since approval is a synchronous request a reviewer is waiting on.
    The approve response must still succeed and the gate must still clear even
    though nothing got indexed."""
    _write_artifact(client, item_at_spec_gate, "---\ntitle: A spec\n---\n\nthe body\n")

    async def _boom(*args, **kwargs):
        raise RuntimeError("index is locked")

    monkeypatch.setattr(api.app.state.indexer, "ingest_gate_artifact", _boom)

    resp = client.post(f"/api/work-items/{item_at_spec_gate}/gates/spec_approval/approve")
    assert resp.status_code == 200

    docs = client.get(f"/api/work-items/{item_at_spec_gate}/documents").json()["documents"]
    assert [d for d in docs if d["source_kind"] == "artifact"] == []


def test_approving_a_gate_does_not_wait_for_a_slow_ingest(client, item_at_spec_gate, monkeypatch):
    """With the vector extra installed and the model host unreachable, the
    embed stalls for minutes. The approval answers without it, and the ingest
    still finishes afterwards."""
    _write_artifact(client, item_at_spec_gate, "---\ntitle: A spec\n---\n\nthe body\n")
    real = api.app.state.indexer.ingest_gate_artifact
    release = threading.Event()

    async def _slow(**kwargs):
        while not release.is_set():
            await asyncio.sleep(0.02)
        await real(**kwargs)

    monkeypatch.setattr(api.app.state.indexer, "ingest_gate_artifact", _slow)

    started = time.monotonic()
    resp = client.post(f"/api/work-items/{item_at_spec_gate}/gates/spec_approval/approve")
    assert resp.status_code == 200
    assert time.monotonic() - started < 5
    docs = client.get(f"/api/work-items/{item_at_spec_gate}/documents").json()["documents"]
    assert [d for d in docs if d["source_kind"] == "artifact"] == []
    release.set()
    assert len(_await_artifact_docs(client, item_at_spec_gate)) == 1


def test_approving_a_gate_with_no_artifact_ingests_nothing(client, item_at_spec_gate):
    """An agent that reported done without honouring the artifact contract
    (Task 8) must not make gate approval 500, and must leave nothing behind
    for this gate's artifact -- there is nothing to remember. (The fake
    agent's own session summary still ingests separately; this only pins the
    artifact side.)"""
    _spec(client, item_at_spec_gate).unlink()

    resp = client.post(f"/api/work-items/{item_at_spec_gate}/gates/spec_approval/approve")
    assert resp.status_code == 200

    docs = client.get(f"/api/work-items/{item_at_spec_gate}/documents").json()["documents"]
    assert [d for d in docs if d["source_kind"] == "artifact"] == []


def test_the_review_gate_still_resolves_its_brief(tmp_path):
    """§5. The shipped `default` chain's final review gate must resolve the
    review brief, or the human is asked to approve a merge request with no
    document. V1: the gate names its own `artifact` (`gate-owns-gate-
    behaviour`); nothing scans the node in front of it any more."""
    from kraft.executor.entry import single_repo_target
    from kraft.policy import InstancePolicy, InstancePolicyInput

    chain = v1_named_chain(tmp_path / "templates", "default").materialize(
        target=single_repo_target(str(tmp_path)),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput.model_validate({})),
    )
    brief = tmp_path / "wt" / "w1" / ".engineering" / "review_briefs" / "w1.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("---\ntitle: The brief\n---\n\nbody\n")

    # An item from before `docs/`: its brief is in its worktree.
    st = SimpleNamespace(run_dirs=SimpleNamespace(base=tmp_path / "run", worktrees=tmp_path / "wt"))
    row = {"id": "w1", "chain_definition": "{}", "materialized_chain": chain.to_json()}

    assert board._gate_artifact(st, row, "final_review") == ".engineering/review_briefs/w1.md"
