from __future__ import annotations

import subprocess

import pytest
from support.harness import make_repo_with_engineering

from kraft.index import db as index_db
from kraft.index import ingest


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


# ---- split_front_matter ----


def test_split_front_matter_present():
    fm, body = ingest.split_front_matter("---\ntitle: Hi\nkind: notes\n---\n# Body\ntext\n")
    assert fm == {"title": "Hi", "kind": "notes"}
    assert body == "# Body\ntext\n"


def test_split_front_matter_absent():
    fm, body = ingest.split_front_matter("# Just a heading\nbody")
    assert fm == {}
    assert body == "# Just a heading\nbody"


def test_split_front_matter_malformed_yaml_ignored():
    fm, body = ingest.split_front_matter("---\n: : not : valid :\n---\nbody\n")
    assert fm == {}
    assert body == "body\n"


def test_split_front_matter_scalar_not_dict():
    fm, body = ingest.split_front_matter("---\njust a string\n---\nbody\n")
    assert fm == {}
    assert body == "body\n"


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


def test_reconcile_tolerates_non_json_front_matter(conn, tmp_path):
    # a YAML date scalar in front-matter must not crash ingestion
    repo = make_repo_with_engineering(
        tmp_path,
        {".engineering/specs/a.md": "---\ntitle: A\ndate: 2026-09-04\ntags: [x, y]\n---\nbody\n"},
    )
    s = ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert s.inserted == 1
    meta = _rows(conn, str(repo))[".engineering/specs/a.md"]["metadata_json"]
    assert "2026-09-04" in meta


# ---- reconcile ----


@pytest.fixture
def conn(tmp_path):
    c = index_db.open_index(tmp_path / "index.db")
    yield c
    c.close()


def _rows(conn, repo):
    return {
        r["path"]: dict(r)
        for r in conn.execute("SELECT * FROM documents WHERE repo=?", (repo,)).fetchall()
    }


def test_reconcile_insert_then_noop(conn, tmp_path):
    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nalpha\n"})
    s1 = ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert (s1.inserted, s1.updated, s1.renamed, s1.deleted) == (1, 0, 0, 0)
    assert conn.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0] == 1

    s2 = ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert (s2.inserted, s2.updated, s2.renamed, s2.deleted) == (0, 0, 0, 0)


def test_reconcile_edit_changes_hash_and_reextracts(conn, tmp_path):
    repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nalpha\n"})
    ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    doc_id = _rows(conn, str(repo))[".engineering/specs/a.md"]["id"]

    (repo / ".engineering/specs/a.md").write_text("# A\nalpha bravo charlie\n")
    _git(repo, "commit", "-am", "edit")
    s = ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert (s.inserted, s.updated, s.renamed, s.deleted) == (0, 1, 0, 0)
    row = _rows(conn, str(repo))[".engineering/specs/a.md"]
    assert row["id"] == doc_id
    assert row["content"] == "# A\nalpha bravo charlie\n"


def test_reconcile_rename_keeps_id(conn, tmp_path):
    repo = make_repo_with_engineering(
        tmp_path, {".engineering/specs/a.md": "# A\nunique body xyzzy\n"}
    )
    ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    doc_id = _rows(conn, str(repo))[".engineering/specs/a.md"]["id"]

    _git(repo, "mv", ".engineering/specs/a.md", ".engineering/specs/renamed.md")
    _git(repo, "commit", "-m", "rename")
    s = ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
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
    ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    _git(repo, "rm", ".engineering/specs/b.md")
    _git(repo, "commit", "-m", "rm b")
    s = ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert (s.inserted, s.updated, s.renamed, s.deleted) == (0, 0, 0, 1)
    assert set(_rows(conn, str(repo))) == {".engineering/specs/a.md"}
    assert conn.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0] == 1
