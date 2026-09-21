from __future__ import annotations

import os

import pytest
from support.harness import make_repo_with_engineering

from kraft.db import Database
from kraft.index import db as index_db
from kraft.index.service import Indexer


def _seed_work_item(db: Database, repo: str, wid: str = "w1"):
    return db.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at) VALUES "
            "(?, 'b1', 't', ?, 'quick-task', '{}', 'active', 'now', 'now')",
            (wid, repo),
        )
    )


async def test_repos_union_of_work_items_and_env(tmp_path, database, conn):
    repo_a = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"}, "a")
    repo_b = make_repo_with_engineering(tmp_path, {".engineering/specs/b.md": "# B\ny\n"}, "b")
    await _seed_work_item(database, str(repo_a))
    ix = Indexer(conn, database, repos_env=f"{repo_b}{os.pathsep}/no/such/dir")
    assert set(ix.repos()) == {str(repo_a), str(repo_b)}


async def test_repos_dedupes_a_symlinked_path_against_its_target(tmp_path, database, conn):
    """Kraft-k7uq: `KRAFT_INDEX_REPOS` (an e2e-only bootstrap path) and a repo
    connected through Settings can name the same directory two different ways
    -- a symlinked tmp root (macOS's `/var` -> `/private/var`) is the case
    that actually happens. Two strings for one repo meant two scans and two
    `documents` rows for the same file; `repos()` must collapse them first."""

    real = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"})
    link = tmp_path / "link-to-real"
    link.symlink_to(real)
    await _seed_work_item(database, str(link))
    ix = Indexer(conn, database, repos_env=str(real))
    assert set(ix.repos()) == {str(real.resolve())}


async def test_startup_scan_then_search(tmp_path, database, conn):
    repo = make_repo_with_engineering(
        tmp_path,
        {
            ".engineering/specs/ws.md": (
                "---\ntitle: WS transport\nowner: omid\n---\nreconnect backoff schedule caps\n"
            ),
            ".engineering/plans/ui.md": "# UI plan\nboard and detail view\n",
        },
    )
    await _seed_work_item(database, str(repo))
    ix = Indexer(conn, database, repos_env="")
    await ix.startup_scan()

    hits = ix.search("reconnect backoff")
    assert [h["path"] for h in hits] == [".engineering/specs/ws.md"]
    assert hits[0]["title"] == "WS transport"
    assert hits[0]["kind"] == "specs"
    assert "[reconnect]" in hits[0]["snippet"]
    assert hits[0]["links"] == []

    assert ix.search("board", kind="plans")
    assert ix.search("board", kind="specs") == []
    assert ix.search("backoff", repo="/other/repo") == []
    assert ix.search("backoff", source_kind="session_summary") == []

    doc = ix.get_document(hits[0]["id"])
    assert doc["content"] == "reconnect backoff schedule caps\n"
    assert doc["metadata"] == {"owner": "omid"}
    assert ix.get_document("nope") is None

    h = ix.health()
    assert h["documents"] == 2
    assert h["repos_scanned"] == 1
    assert h["errors"] == []
    assert h["last_scan_at"]


async def test_search_punctuated_terms_match_literally(tmp_path, database, conn):
    """Kraft-bj9.4: hyphens/dots are FTS5 syntax, not text — retry them quoted."""

    repo = make_repo_with_engineering(
        tmp_path,
        {
            ".engineering/specs/e4.md": "# effort-4a\nindexer touches api.py\n",
            ".engineering/plans/other.md": "# other\nunrelated prose\n",
        },
    )
    await _seed_work_item(database, str(repo))
    ix = Indexer(conn, database, repos_env="")
    await ix.startup_scan()

    assert [h["path"] for h in ix.search("effort-4a")] == [".engineering/specs/e4.md"]
    assert [h["path"] for h in ix.search("api.py")] == [".engineering/specs/e4.md"]
    # multi-term punctuated query keeps AND semantics across terms
    assert [h["path"] for h in ix.search("effort-4a api.py")] == [".engineering/specs/e4.md"]
    assert ix.search("effort-4a nonexistentword") == []
    # a plain query still goes through the unquoted path untouched
    assert [h["path"] for h in ix.search("indexer")] == [".engineering/specs/e4.md"]


async def test_search_unparseable_query_falls_back_to_literal(database, conn):
    """An unterminated quote is treated as text, not a 422."""

    ix = Indexer(conn, database, repos_env="")
    assert ix.search('"unterminated') == []


def _seed_session(db, sid, wid, ref, node="implementation", hook="on.implementation.start"):
    from kraft import store

    return db.write(
        lambda c: (
            store.create_session(
                c,
                id=sid,
                work_item_id=wid,
                node_id=node,
                hook_point=hook,
                log_path="/dev/null",
                result_path="/dev/null",
            ),
            c.execute("UPDATE worker_sessions SET session_summary_ref=? WHERE id=?", (ref, sid)),
        )
    )


async def test_ingest_session_summary_from_worktree(tmp_path, database, conn):
    from kraft.paths import RunDirs

    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"})
    await _seed_work_item(database, str(repo))
    rd = RunDirs(tmp_path / "run").ensure()
    wt = rd.worktrees / "w1" / ".engineering" / "sessions"
    wt.mkdir(parents=True)
    (wt / "s1.md").write_text(
        "---\ntitle: Implementation session\nwork_item_ids: [w1, w2]\n"
        "node_id: bogus\nhook_point: on.bogus\nworker_session_id: s1\n---\n"
        "rewrote the adapter\n"
    )
    await _seed_session(database, "s1", "w1", ".engineering/sessions/s1.md")

    ix = Indexer(conn, database, repos_env="", run_dirs=rd)
    assert await ix.ingest_session_summary("s1") is True

    hits = ix.search("adapter")
    assert [h["path"] for h in hits] == [".engineering/sessions/s1.md"]
    assert hits[0]["source_kind"] == "session_summary"
    assert hits[0]["repo"] == str(repo)
    assert hits[0]["title"] == "Implementation session"

    doc_id = hits[0]["id"]
    rows = conn.execute(
        "SELECT work_item_id, node_id, hook_point, worker_session_id "
        "FROM document_links WHERE document_id=? ORDER BY COALESCE(work_item_id, '')",
        (doc_id,),
    ).fetchall()
    # DB wins for the session triple; front-matter's bogus values are dropped
    assert rows[0]["node_id"] == "implementation"
    assert rows[0]["hook_point"] == "on.implementation.start"
    assert rows[0]["worker_session_id"] == "s1"
    # front-matter work items unioned with the session's own
    assert sorted(r["work_item_id"] for r in rows if r["work_item_id"]) == ["w1", "w2"]

    # idempotent
    assert await ix.ingest_session_summary("s1") is True
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM document_links WHERE document_id=?", (doc_id,)
        ).fetchone()[0]
        == 3
    )


async def test_ingest_session_summary_missing_file_is_false(tmp_path, database, conn):
    from kraft.paths import RunDirs

    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"})
    await _seed_work_item(database, str(repo))
    await _seed_session(database, "s1", "w1", ".engineering/sessions/nope.md")
    ix = Indexer(conn, database, repos_env="", run_dirs=RunDirs(tmp_path / "run").ensure())
    assert await ix.ingest_session_summary("s1") is False
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


async def test_ingest_session_summary_rejects_path_escape(tmp_path, database, conn):
    from kraft.paths import RunDirs

    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"})
    await _seed_work_item(database, str(repo))
    rd = RunDirs(tmp_path / "run").ensure()
    (tmp_path / "secret.md").write_text("# secret\ndo not index\n")
    await _seed_session(database, "s1", "w1", "../../secret.md")
    await _seed_session(database, "s2", "w1", "/etc/hosts")
    ix = Indexer(conn, database, repos_env="", run_dirs=rd)
    assert await ix.ingest_session_summary("s1") is False
    assert await ix.ingest_session_summary("s2") is False
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


async def test_links_resolved_in_search_and_document_and_by_work_item(tmp_path, database, conn):
    from kraft.paths import RunDirs

    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"})
    await _seed_work_item(database, str(repo))
    rd = RunDirs(tmp_path / "run").ensure()
    sessions = rd.worktrees / "w1" / ".engineering" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "s1.md").write_text("# Session\nlinked prose\n")
    await _seed_session(database, "s1", "w1", ".engineering/sessions/s1.md")

    ix = Indexer(conn, database, repos_env="", run_dirs=rd)
    await ix.startup_scan()
    await ix.ingest_session_summary("s1")

    hit = ix.search("linked")[0]
    assert {ln["work_item_id"] for ln in hit["links"] if ln["work_item_id"]} == {"w1"}
    assert any(ln["worker_session_id"] == "s1" for ln in hit["links"])

    doc = ix.get_document(hit["id"])
    assert doc["links"] == hit["links"]

    # an artifact has no links, and still reports an empty list
    spec = ix.search("x")[0]
    assert spec["links"] == []

    docs = ix.documents_for_work_item("w1")
    assert [d["path"] for d in docs] == [".engineering/sessions/s1.md"]
    assert docs[0]["title"] == "Session"
    assert docs[0]["source_kind"] == "session_summary"
    assert ix.documents_for_work_item("nope") == []


async def test_documents_for_work_item_includes_intake_attachments(tmp_path, database, conn):
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# Plan\nbody\n"})
    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at, attachments) VALUES "
            "(?, 'b1', 't', ?, 'default', '{}', 'active', 'now', 'now', ?)",
            ("w1", str(repo), '[{"kind": "plan", "path": ".engineering/plans/p.md"}]'),
        )
    )
    ix = Indexer(conn, database, repos_env=str(repo))
    await ix.rescan_repo(str(repo))
    docs = ix.documents_for_work_item("w1")
    assert [d["path"] for d in docs] == [".engineering/plans/p.md"]
    assert docs[0]["attachment_kind"] == "plan"


async def test_documents_for_work_item_shows_unmerged_attachment_from_its_worktree(
    tmp_path, database, conn, repo
):
    """The attachment is committed only on the item's own branch/worktree, not
    on the registered main checkout — `rescan_repo` never sees it, but the
    worktree fallback should still surface it (Kraft-3sbq)."""
    from kraft.paths import RunDirs

    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at, attachments) VALUES "
            "(?, 'b1', 't', ?, 'default', '{}', 'active', 'now', 'now', ?)",
            ("w1", str(repo), '[{"kind": "plan", "path": ".engineering/plans/p.md"}]'),
        )
    )
    rd = RunDirs(tmp_path / "run").ensure()
    wt = rd.worktrees / "w1" / ".engineering" / "plans"
    wt.mkdir(parents=True)
    (wt / "p.md").write_text("# Attached plan\nbody\n")

    ix = Indexer(conn, database, repos_env=str(repo), run_dirs=rd)
    await ix.rescan_repo(str(repo))
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0

    docs = ix.documents_for_work_item("w1")
    assert [d["path"] for d in docs] == [".engineering/plans/p.md"]
    assert docs[0]["attachment_kind"] == "plan"
    assert docs[0]["title"] == "Attached plan"
    assert docs[0]["document_id"] == "attachment:w1:plan"

    doc = ix.get_document("attachment:w1:plan")
    assert doc is not None
    assert doc["content"] == "# Attached plan\nbody\n"
    assert doc["title"] == "Attached plan"


async def test_resolve_attachment_path_reads_from_the_worktree_not_the_main_repo(
    tmp_path, database, conn, repo
):
    """Kraft-2jy6: `open_document` needs the same worktree-first path
    `_synthesize_attachment_doc` already resolves content from — the file is
    on the item's branch, not the registered repo's checkout."""
    from kraft.paths import RunDirs

    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at, attachments) VALUES "
            "(?, 'b1', 't', ?, 'default', '{}', 'active', 'now', 'now', ?)",
            ("w1", str(repo), '[{"kind": "plan", "path": ".engineering/plans/p.md"}]'),
        )
    )
    rd = RunDirs(tmp_path / "run").ensure()
    wt = rd.worktrees / "w1" / ".engineering" / "plans"
    wt.mkdir(parents=True)
    (wt / "p.md").write_text("# Attached plan\nbody\n")

    ix = Indexer(conn, database, repos_env=str(repo), run_dirs=rd)
    resolved = ix.resolve_attachment_path("attachment:w1:plan")
    assert resolved == (wt / "p.md").resolve()
    assert ix.resolve_attachment_path("attachment:w1:spec") is None
    assert ix.resolve_attachment_path("attachment:nope:plan") is None


async def test_documents_for_work_item_prefers_indexed_over_worktree_attachment(
    tmp_path, database, conn
):
    """Once the attachment lands in the `documents` table (post-merge, or a
    path that already exists in the main checkout), that row wins — no
    duplicate entry from the worktree fallback."""
    from kraft.paths import RunDirs

    repo = make_repo_with_engineering(
        tmp_path, {".engineering/plans/p.md": "# Indexed plan\nbody\n"}
    )
    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at, attachments) VALUES "
            "(?, 'b1', 't', ?, 'default', '{}', 'active', 'now', 'now', ?)",
            ("w1", str(repo), '[{"kind": "plan", "path": ".engineering/plans/p.md"}]'),
        )
    )
    rd = RunDirs(tmp_path / "run").ensure()
    wt = rd.worktrees / "w1" / ".engineering" / "plans"
    wt.mkdir(parents=True)
    (wt / "p.md").write_text("# Stale worktree copy\nbody\n")

    ix = Indexer(conn, database, repos_env=str(repo), run_dirs=rd)
    await ix.rescan_repo(str(repo))
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1

    docs = ix.documents_for_work_item("w1")
    assert len(docs) == 1
    assert docs[0]["title"] == "Indexed plan"
    assert not str(docs[0]["document_id"]).startswith("attachment:")


async def test_documents_for_work_item_sorted_by_time_desc(tmp_path, database, conn):
    """Newest-indexed first, not alphabetical by path — a rescan's arrival
    order is the only time signal common to every document kind."""

    repo = make_repo_with_engineering(
        tmp_path,
        {
            ".engineering/specs/a-first.md": "---\nwork_item_ids: [w1]\n---\nA\n",
            ".engineering/specs/z-second.md": "---\nwork_item_ids: [w1]\n---\nZ\n",
        },
    )
    await _seed_work_item(database, str(repo))
    ix = Indexer(conn, database, repos_env=str(repo))
    await ix.rescan_repo(str(repo))
    # Pin indexed_at directly: both rows land in the same rescan, so their
    # real timestamps can tie at second resolution.
    conn.execute(
        "UPDATE documents SET indexed_at = ? WHERE path = ?",
        ("2020-01-01T00:00:00Z", ".engineering/specs/a-first.md"),
    )
    conn.execute(
        "UPDATE documents SET indexed_at = ? WHERE path = ?",
        ("2020-06-01T00:00:00Z", ".engineering/specs/z-second.md"),
    )
    conn.commit()
    docs = ix.documents_for_work_item("w1")
    assert [d["path"] for d in docs] == [
        ".engineering/specs/z-second.md",
        ".engineering/specs/a-first.md",
    ]


async def test_documents_for_work_item_survives_a_rescan(tmp_path, database, conn):
    """Attachments are joined at read time, so re-ingesting the document — which
    rewrites its document_links wholesale — cannot drop them."""

    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# Plan\nbody\n"})
    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at, attachments) VALUES "
            "(?, 'b1', 't', ?, 'default', '{}', 'active', 'now', 'now', ?)",
            ("w1", str(repo), '[{"kind": "plan", "path": ".engineering/plans/p.md"}]'),
        )
    )
    ix = Indexer(conn, database, repos_env=str(repo))
    await ix.rescan_repo(str(repo))
    await ix.rescan_repo(str(repo))
    assert len(ix.documents_for_work_item("w1")) == 1


# ---- 4C: fusion + vector/hybrid modes ----


def test_rrf_fuses_ranks_and_rewards_agreement():
    from kraft.index.service import rrf

    fts = ["a", "b", "c"]
    vec = ["b", "a", "d"]
    scores = rrf(fts, vec, k=60)
    # b is 2nd + 1st, a is 1st + 2nd -> tie; both beat singletons c and d
    assert scores["a"] == pytest.approx(scores["b"])
    assert scores["a"] > scores["c"] > 0
    assert "d" in scores


def test_rrf_keeps_documents_present_in_only_one_list():
    from kraft.index.service import rrf

    scores = rrf(["only_fts"], [], k=60)
    assert set(scores) == {"only_fts"}


class _StubEmbedder:
    """Deterministic 384-d vectors: identical text embeds identically."""

    model_name = "stub-model"

    def __init__(self, available=True):
        self._available = available
        self.reason = None if available else "stub unavailable"

    def available(self):
        return self._available

    def _vec(self, text):
        v = [0.0] * 384
        for i, ch in enumerate(text.lower().encode()[:384]):
            v[i] = ch / 255.0
        return v

    def try_encode(self, texts):
        return [self._vec(t) for t in texts] if self._available else None

    def encode(self, texts):
        if not self._available:
            raise RuntimeError(self.reason)
        return [self._vec(t) for t in texts]


def _indexed(tmp_path, state, embedder):
    repo = make_repo_with_engineering(
        tmp_path,
        {
            ".engineering/specs/ws.md": "# WS\nreconnect backoff schedule caps\n",
            ".engineering/plans/ui.md": "# UI plan\nboard and detail view\n",
        },
    )
    ix = Indexer(conn_for(tmp_path), state, repos_env=str(repo))
    ix._embedder = embedder
    return repo, ix


def conn_for(tmp_path):
    return index_db.open_index(tmp_path / "index.db")


async def test_vector_mode_without_an_embedder_is_refused(tmp_path, database):
    repo, ix = _indexed(tmp_path, database, _StubEmbedder(available=False))
    await ix.startup_scan()
    with pytest.raises(RuntimeError):
        ix.search("reconnect", mode="vector")


async def test_hybrid_without_an_embedder_degrades_to_fts(tmp_path, database):
    repo, ix = _indexed(tmp_path, database, _StubEmbedder(available=False))
    await ix.startup_scan()
    hits, served = ix.search_with_mode("reconnect", mode="hybrid")
    assert served == "fts"
    assert [h["path"] for h in hits] == [".engineering/specs/ws.md"]


async def test_vector_search_finds_documents_and_respects_filters(tmp_path, database):
    repo, ix = _indexed(tmp_path, database, _StubEmbedder())
    await ix.startup_scan()

    hits = ix.search("reconnect backoff schedule caps", mode="vector")
    assert hits, "vector search returned nothing"
    assert any(h["path"] == ".engineering/specs/ws.md" for h in hits)

    # the filters must apply to the vector leg, not only to FTS
    assert ix.search("reconnect backoff schedule caps", mode="vector", kind="plans") == [
        h for h in ix.search("reconnect backoff schedule caps", mode="vector", kind="plans")
    ]
    assert all(
        h["kind"] == "plans"
        for h in ix.search("board and detail view", mode="vector", kind="plans")
    )
    assert ix.search("board", mode="vector", repo="/nope") == []


async def test_hybrid_returns_results_from_both_legs(tmp_path, database):
    repo, ix = _indexed(tmp_path, database, _StubEmbedder())
    await ix.startup_scan()
    hits, served = ix.search_with_mode("reconnect backoff schedule caps", mode="hybrid")
    assert served == "hybrid"
    assert any(h["path"] == ".engineering/specs/ws.md" for h in hits)
    assert all("links" in h for h in hits)


async def test_health_reports_embeddings(tmp_path, database):
    repo, ix = _indexed(tmp_path, database, _StubEmbedder())
    await ix.startup_scan()
    emb = ix.health()["embeddings"]
    assert emb["available"] is True
    assert emb["model"]
    assert emb["chunks"] > 0


async def test_repos_includes_connected_repos_with_no_work_items(tmp_path, database, conn):
    """Kraft-38w: a repo connected through Settings is indexable on first use,
    before any work item exists for it."""

    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/c.md": "# C\nz\n"}, "c")
    repos_yaml = tmp_path / "repos.yaml"
    repos_yaml.write_text(f"repos:\n  - path: {repo}\n    name: c\n")
    ix = Indexer(conn, database, repos_env="", repos_path=repos_yaml)
    assert ix.repos() == [str(repo)]

    # ... and a malformed file degrades to the other sources.
    repos_yaml.write_text("repos: nope\n")
    await _seed_work_item(database, str(repo), "w-c")
    assert ix.repos() == [str(repo)]


async def test_purge_repo_drops_its_documents(tmp_path, database, conn):
    repo = make_repo_with_engineering(
        tmp_path, {".engineering/specs/d.md": "# D\nharpoon rigging\n"}, "d"
    )
    ix = Indexer(conn, database, repos_env=str(repo))
    await ix.startup_scan()
    assert ix.search("harpoon rigging")

    await ix.purge_repo(str(repo))
    assert ix.search("harpoon rigging") == []


async def test_purge_repo_drops_event_ingested_rows_too(database, conn, repo):
    """A disconnected repo must stop answering searches for everything, not
    just what a git scan would have found. `reconcile` deliberately never
    deletes an `origin='event_ingest'` row (a gate artifact or session
    summary) on its own -- that is what stops a rescan from treating one as
    stale the instant it is written, since nothing under `.engineering/` is
    committed for a scan to reproduce. `purge_repo` must not inherit that
    same protection: disconnect is the one place that row really is gone."""

    ix = Indexer(conn, database, repos_env=str(repo))
    await ix.startup_scan()
    await ix.ingest_gate_artifact(
        repo=str(repo),
        work_item_id="w1",
        path=".engineering/specs/w1.md",
        content="# spec\nnarwhal tusk provenance\n",
    )
    assert ix.search("narwhal tusk provenance")

    await ix.purge_repo(str(repo))
    assert ix.search("narwhal tusk provenance") == []
