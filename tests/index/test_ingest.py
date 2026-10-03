from __future__ import annotations

import asyncio
import os
import threading

import pytest
from support.harness import commit_all, git, make_repo_with_engineering

from kraft.index import ingest


def _reconcile(*args, **kwargs):
    """`ingest.reconcile` is async (the embedding step runs in a thread — see
    Indexer._write_lock); these tests stay sync callers via a fresh loop each
    call, same as the rest of this file's style."""
    return asyncio.run(ingest.reconcile(*args, **kwargs))


# ---- split_front_matter ----


def test_split_front_matter_present():
    fm, body = ingest.split_front_matter("---\ntitle: Hi\nkind: notes\n---\n# Body\ntext\n")
    assert fm == {"title": "Hi", "kind": "notes"}
    assert body == "# Body\ntext\n"


@pytest.mark.parametrize(
    ("text", "body"),
    [
        ("# Just a heading\nbody", "# Just a heading\nbody"),
        ("---\n: : not : valid :\n---\nbody\n", "body\n"),
        ("---\njust a string\n---\nbody\n", "body\n"),
    ],
    ids=["absent", "malformed-yaml-ignored", "scalar-not-dict"],
)
def test_split_front_matter_without_a_mapping_yields_none(text, body):
    assert ingest.split_front_matter(text) == ({}, body)


# ---- derive_kind / derive_title ----


def test_derive_kind_from_folder():
    assert ingest.derive_kind(".engineering/specs/a.md", {}) == "specs"
    assert ingest.derive_kind(".engineering/specs/sub/a.md", {}) == "specs"
    assert ingest.derive_kind(".engineering/a.md", {}) is None


def test_derive_kind_front_matter_overrides():
    assert ingest.derive_kind(".engineering/specs/a.md", {"kind": "adr"}) == "adr"


def test_derive_title_precedence():
    assert ingest.derive_title("x/a.md", {"title": "FM"}, "# Heading\n") == "FM"
    assert ingest.derive_title("x/a.md", {}, "\n#  Heading here \nmore") == "Heading here"
    assert ingest.derive_title("x/my-doc.md", {}, "no heading here") == "my-doc"


# ---- scan_repo ----


def test_scan_repo_finds_nested_md_ignores_others(tmp_path):
    repo = make_repo_with_engineering(
        tmp_path,
        {
            ".engineering/specs/one.md": "---\ntitle: One\n---\nalpha bravo\n",
            ".engineering/plans/two.md": "# Two\ncharlie delta\n",
            ".engineering/notes.txt": "not markdown",
            "README.md": "not under .engineering",
        },
    )
    docs = {d.path: d for d in ingest.scan_repo(repo)}
    assert set(docs) == {".engineering/specs/one.md", ".engineering/plans/two.md"}
    assert docs[".engineering/specs/one.md"].kind == "specs"
    assert docs[".engineering/specs/one.md"].title == "One"
    assert docs[".engineering/plans/two.md"].kind == "plans"
    assert docs[".engineering/plans/two.md"].title == "Two"
    assert docs[".engineering/specs/one.md"].source_updated_at


def test_scan_repo_non_git_dir_returns_empty(tmp_path):
    (tmp_path / "plain").mkdir()
    assert ingest.scan_repo(tmp_path / "plain") == []


SECRET = "SECRET_TOKEN=ghp_FAKEFAKEFAKE1234567890\n"


def _symlink_out(tmp_path, repo):
    """A committed symlink to a file outside the repo, as a malicious repo or
    a worker's merged branch could carry."""
    (tmp_path / "secret.txt").write_text(SECRET)
    (repo / ".engineering/specs/leak.md").symlink_to(tmp_path / "secret.txt")
    commit_all(repo)


def _symlink_in(tmp_path, repo):
    """A committed symlink to an untracked, ignored file inside the repo."""
    (repo / ".gitignore").write_text(".env\n")
    (repo / ".env").write_text(SECRET)
    (repo / ".engineering/specs/leak.md").symlink_to("../../.env")
    commit_all(repo)


def _directory_out(tmp_path, repo):
    """A listed document under a directory swapped for a symlink since: git
    cannot commit that, but the checkout on disk can hold it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text(SECRET)
    (repo / ".engineering/notes/leak.md").parent.mkdir(parents=True)
    (repo / ".engineering/notes/leak.md").write_text("# placeholder\n")
    commit_all(repo)
    for f in (repo / ".engineering/notes").iterdir():
        f.unlink()
    (repo / ".engineering/notes").rmdir()
    (repo / ".engineering/notes").symlink_to(outside)


@pytest.mark.parametrize(
    "plant", [_symlink_out, _symlink_in, _directory_out], ids=["out", "in", "directory"]
)
def test_scan_repo_never_reads_through_a_symlink(tmp_path, plant):
    """Search serves what the index holds. Nothing reached through a symlink
    is indexed, even one pointing inside the repo: it can name a file git
    never tracked, like `.env`."""
    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/kept.md": "# Kept\n"})
    plant(tmp_path, repo)
    docs = ingest.scan_repo(repo)
    assert [d.path for d in docs] == [".engineering/specs/kept.md"]
    assert not [d for d in docs if "ghp_" in d.content]


def test_scan_repo_skips_a_fifo_at_once(tmp_path):
    """A listed document swapped for a FIFO: read blocking, it waits for a
    writer that never comes, and the indexer with it."""
    repo = make_repo_with_engineering(
        tmp_path,
        {".engineering/specs/kept.md": "# Kept\n", ".engineering/specs/pipe.md": "# Pipe\n"},
    )
    fifo = repo / ".engineering/specs/pipe.md"
    fifo.unlink()
    os.mkfifo(fifo)
    found = []
    scan = threading.Thread(target=lambda: found.extend(ingest.scan_repo(repo)), daemon=True)
    scan.start()
    scan.join(10)
    if scan.is_alive():  # free it before failing: a writer ends its wait
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
    assert not scan.is_alive(), "scan_repo blocked on a FIFO"
    assert [d.path for d in found] == [".engineering/specs/kept.md"]


def test_scan_repo_never_runs_the_repos_fsmonitor(tmp_path, monkeypatch):
    """`core.fsmonitor` is a program `git ls-files` runs, named by the repo's
    own config. Outside the server nothing else pins it off."""
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\n"})
    ran = tmp_path / "fsmonitor-ran"
    monitor = tmp_path / "monitor.sh"
    monitor.write_text(f"#!/bin/sh\ntouch {ran}\n")
    monitor.chmod(0o755)
    git(repo, "config", "core.fsmonitor", str(monitor))
    assert [d.path for d in ingest.scan_repo(repo)] == [".engineering/specs/a.md"]
    assert not ran.exists()


def test_reconcile_tolerates_non_json_front_matter(conn, tmp_path):
    # a YAML date scalar in front-matter must not crash ingestion
    repo = make_repo_with_engineering(
        tmp_path,
        {".engineering/specs/a.md": "---\ntitle: A\ndate: 2026-09-04\ntags: [x, y]\n---\nbody\n"},
    )
    s = _reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert s.inserted == 1
    meta = _rows(conn, str(repo))[".engineering/specs/a.md"]["metadata_json"]
    assert "2026-09-04" in meta


# ---- reconcile ----


def _rows(conn, repo):
    return {
        r["path"]: dict(r)
        for r in conn.execute("SELECT * FROM documents WHERE repo=?", (repo,)).fetchall()
    }


def test_reconcile_insert_then_noop(conn, tmp_path):
    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nalpha\n"})
    s1 = _reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert (s1.inserted, s1.updated, s1.renamed, s1.deleted) == (1, 0, 0, 0)
    assert conn.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0] == 1

    s2 = _reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert (s2.inserted, s2.updated, s2.renamed, s2.deleted) == (0, 0, 0, 0)


def test_reconcile_edit_changes_hash_and_reextracts(conn, tmp_path):
    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nalpha\n"})
    _reconcile(conn, str(repo), ingest.scan_repo(repo))
    doc_id = _rows(conn, str(repo))[".engineering/specs/a.md"]["id"]

    (repo / ".engineering/specs/a.md").write_text("# A\nalpha bravo charlie\n")
    git(repo, "commit", "-am", "edit")
    s = _reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert (s.inserted, s.updated, s.renamed, s.deleted) == (0, 1, 0, 0)
    row = _rows(conn, str(repo))[".engineering/specs/a.md"]
    assert row["id"] == doc_id
    assert row["content"] == "# A\nalpha bravo charlie\n"


def test_reconcile_rename_keeps_id(conn, tmp_path):
    repo = make_repo_with_engineering(
        tmp_path, {".engineering/specs/a.md": "# A\nunique body xyzzy\n"}
    )
    _reconcile(conn, str(repo), ingest.scan_repo(repo))
    doc_id = _rows(conn, str(repo))[".engineering/specs/a.md"]["id"]

    git(repo, "mv", ".engineering/specs/a.md", ".engineering/specs/renamed.md")
    git(repo, "commit", "-m", "rename")
    s = _reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert (s.inserted, s.updated, s.renamed, s.deleted) == (0, 0, 1, 0)
    rows = _rows(conn, str(repo))
    assert ".engineering/specs/a.md" not in rows
    assert rows[".engineering/specs/renamed.md"]["id"] == doc_id
    assert conn.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0] == 1


def test_reconcile_delete_removes_row_and_fts(conn, tmp_path):
    repo = make_repo_with_engineering(
        tmp_path,
        {
            ".engineering/specs/a.md": "# A\nkeep me\n",
            ".engineering/specs/b.md": "# B\ndelete me\n",
        },
    )
    _reconcile(conn, str(repo), ingest.scan_repo(repo))
    git(repo, "rm", ".engineering/specs/b.md")
    git(repo, "commit", "-m", "rm b")
    s = _reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert (s.inserted, s.updated, s.renamed, s.deleted) == (0, 0, 0, 1)
    assert set(_rows(conn, str(repo))) == {".engineering/specs/a.md"}
    assert conn.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0] == 1


# ---- source_kind_for / links_from_front_matter ----


def test_source_kind_for_sessions_folder():
    assert ingest.source_kind_for(".engineering/sessions/s1.md") == "session_summary"
    assert ingest.source_kind_for(".engineering/specs/a.md") == "artifact"
    assert ingest.source_kind_for("README.md") == "artifact"


def test_links_from_front_matter_full():
    links = ingest.links_from_front_matter(
        {
            "work_item_ids": ["w1", "w2"],
            "node_id": "verify",
            "hook_point": "on.test.run",
            "worker_session_id": "s1",
        }
    )
    assert [x.work_item_id for x in links if x.work_item_id] == ["w1", "w2"]
    session_rows = [x for x in links if x.worker_session_id]
    assert len(session_rows) == 1
    assert session_rows[0].node_id == "verify"
    assert session_rows[0].hook_point == "on.test.run"
    assert session_rows[0].work_item_id is None


def test_links_from_front_matter_scalar_work_item_id():
    links = ingest.links_from_front_matter({"work_item_ids": "w1"})
    assert [x.work_item_id for x in links] == ["w1"]


def test_links_from_front_matter_ignores_malformed():
    assert ingest.links_from_front_matter({}) == []
    assert ingest.links_from_front_matter({"work_item_ids": [None, 3, "w1"]}) == [
        ingest.LinkRow(work_item_id="w1")
    ]
    assert ingest.links_from_front_matter({"node_id": 7}) == []


def test_scan_repo_classifies_sessions(tmp_path):
    repo = make_repo_with_engineering(
        tmp_path,
        {
            ".engineering/specs/a.md": "# A\nspec body\n",
            ".engineering/sessions/s1.md": (
                "---\nwork_item_ids: [w1]\nnode_id: verify\n"
                "hook_point: on.test.run\nworker_session_id: s1\n---\nsummary body\n"
            ),
        },
    )
    by_path = {d.path: d for d in ingest.scan_repo(repo)}
    assert by_path[".engineering/specs/a.md"].source_kind == "artifact"
    assert by_path[".engineering/specs/a.md"].links == ()
    summary = by_path[".engineering/sessions/s1.md"]
    assert summary.source_kind == "session_summary"
    assert summary.kind == "sessions"
    assert [x.work_item_id for x in summary.links if x.work_item_id] == ["w1"]


def test_a_spec_with_work_item_ids_is_linked(tmp_path):
    repo = make_repo_with_engineering(
        tmp_path,
        {
            ".engineering/specs/w1.md": (
                "---\nwork_item_ids: [w1]\nkind: specs\ntitle: A spec\n---\n\nbody\n"
            )
        },
    )
    doc = next(d for d in ingest.scan_repo(repo) if d.path.endswith("specs/w1.md"))
    assert [link.work_item_id for link in doc.links] == ["w1"]


def _all_docs(conn):
    return {r["path"]: r for r in conn.execute("SELECT * FROM documents ORDER BY path").fetchall()}


def _summary(path, content_hash, links=(), title="S1"):
    return ingest.ScannedDoc(
        path=path,
        kind="sessions",
        title=title,
        content="summary",
        content_hash=content_hash,
        metadata={},
        source_created_at=None,
        source_updated_at=None,
        source_kind="session_summary",
        links=links,
    )


def _artifact(path, content_hash, title="A"):
    return ingest.ScannedDoc(
        path=path,
        kind="specs",
        title=title,
        content="spec",
        content_hash=content_hash,
        metadata={},
        source_created_at=None,
        source_updated_at=None,
    )


def test_reconcile_writes_the_links_it_is_given(conn):
    scanned = [
        _artifact(".engineering/specs/a.md", "h1"),
        _summary(
            ".engineering/sessions/s1.md",
            "h2",
            links=(
                ingest.LinkRow(work_item_id="w1"),
                ingest.LinkRow(node_id="verify", hook_point="on.test.run", worker_session_id="s1"),
            ),
        ),
    ]
    stats = _reconcile(conn, "/r", scanned)
    assert stats.inserted == 2
    docs = _all_docs(conn)
    assert docs[".engineering/sessions/s1.md"]["source_kind"] == "session_summary"
    assert docs[".engineering/specs/a.md"]["source_kind"] == "artifact"
    rows = conn.execute(
        "SELECT work_item_id, node_id FROM document_links WHERE document_id=? "
        "ORDER BY COALESCE(work_item_id, '')",
        (docs[".engineering/sessions/s1.md"]["id"],),
    ).fetchall()
    assert [(r["work_item_id"], r["node_id"]) for r in rows] == [(None, "verify"), ("w1", None)]
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM document_links WHERE document_id=?",
            (docs[".engineering/specs/a.md"]["id"],),
        ).fetchone()[0]
        == 0
    )


def test_reconcile_never_deletes_summaries_but_still_deletes_artifacts(conn):
    _reconcile(
        conn,
        "/r",
        [_artifact(".engineering/specs/a.md", "h1"), _summary(".engineering/sessions/s1.md", "h2")],
    )
    stats = _reconcile(conn, "/r", [])
    assert stats.deleted == 1  # the artifact only
    assert set(_all_docs(conn)) == {".engineering/sessions/s1.md"}


def test_reconcile_summary_update_replaces_links(conn):
    p = ".engineering/sessions/s1.md"
    _reconcile(conn, "/r", [_summary(p, "h1", links=(ingest.LinkRow(work_item_id="w1"),))])
    _reconcile(conn, "/r", [_summary(p, "h2", links=(ingest.LinkRow(work_item_id="w2"),))])
    doc_id = _all_docs(conn)[p]["id"]
    rows = conn.execute(
        "SELECT work_item_id FROM document_links WHERE document_id=?", (doc_id,)
    ).fetchall()
    assert [r["work_item_id"] for r in rows] == ["w2"]
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def _chunk_rows(conn, doc_id):
    return conn.execute(
        "SELECT chunk_index, chunk_text FROM document_chunks WHERE document_id=? "
        "ORDER BY chunk_index",
        (doc_id,),
    ).fetchall()


def test_reconcile_writes_chunks(conn):
    _reconcile(conn, "/r", [_artifact(".engineering/specs/a.md", "h1")])
    doc_id = _all_docs(conn)[".engineering/specs/a.md"]["id"]
    rows = _chunk_rows(conn, doc_id)
    assert len(rows) == 1
    assert rows[0]["chunk_text"] == "spec"


def test_changed_content_replaces_chunks(conn):
    p = ".engineering/specs/a.md"
    _reconcile(conn, "/r", [_artifact(p, "h1")])
    doc_id = _all_docs(conn)[p]["id"]
    changed = ingest.ScannedDoc(
        path=p,
        kind="specs",
        title="A",
        content="# One\n\nfirst\n\n# Two\n\nsecond\n",
        content_hash="h2",
        metadata={},
        source_created_at=None,
        source_updated_at=None,
    )
    _reconcile(conn, "/r", [changed])
    rows = _chunk_rows(conn, doc_id)
    assert len(rows) == 2
    assert [r["chunk_index"] for r in rows] == [0, 1]
    assert "first" in rows[0]["chunk_text"]


class _FakeEmbedder:
    """One constant 384-wide vector per chunk: enough for vec0 to hold a row."""

    def try_encode(self, texts):
        return [[0.1] * 384 for _ in texts]


def test_deleting_a_document_leaves_no_chunks_or_vectors(conn):
    _reconcile(conn, "/r", [_artifact(".engineering/specs/a.md", "h1")], embedder=_FakeEmbedder())
    assert conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM document_vectors").fetchone()[0] == 1
    _reconcile(conn, "/r", [])
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM document_vectors").fetchone()[0] == 0


def test_chunks_written_without_an_embedder_and_no_vectors(conn):
    _reconcile(conn, "/r", [_artifact(".engineering/specs/a.md", "h1")], embedder=None)
    assert conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM document_vectors").fetchone()[0] == 0


def test_vectors_written_when_an_embedder_is_supplied(conn):
    _reconcile(conn, "/r", [_artifact(".engineering/specs/a.md", "h1")], embedder=_FakeEmbedder())
    doc_id = _all_docs(conn)[".engineering/specs/a.md"]["id"]
    n = conn.execute(
        "SELECT COUNT(*) FROM document_vectors WHERE chunk_id IN "
        "(SELECT id FROM document_chunks WHERE document_id=?)",
        (doc_id,),
    ).fetchone()[0]
    assert n == 1


def test_embedder_failure_still_keeps_the_document_and_chunks(conn):
    class BrokenEmbedder:
        def try_encode(self, texts):
            return None  # model unavailable

    _reconcile(conn, "/r", [_artifact(".engineering/specs/a.md", "h1")], embedder=BrokenEmbedder())
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM document_vectors").fetchone()[0] == 0
