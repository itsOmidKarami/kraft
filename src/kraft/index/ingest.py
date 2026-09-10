from __future__ import annotations

import asyncio
import hashlib
import json
import re
import struct
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

from kraft.events import _now
from kraft.index.chunk import chunk_markdown

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_ATX_HEADING = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)

SESSIONS_DIR = ".engineering/sessions"


@dataclass(frozen=True)
class LinkRow:
    work_item_id: str | None = None
    node_id: str | None = None
    hook_point: str | None = None
    worker_session_id: str | None = None


@dataclass(frozen=True)
class ScannedDoc:
    path: str
    kind: str | None
    title: str
    content: str
    content_hash: str
    metadata: dict
    source_created_at: str | None
    source_updated_at: str | None
    source_kind: str = "artifact"
    links: tuple[LinkRow, ...] = ()


@dataclass(frozen=True)
class ReconcileStats:
    inserted: int = 0
    updated: int = 0
    renamed: int = 0
    deleted: int = 0


def split_front_matter(text: str) -> tuple[dict, str]:
    m = _FRONT_MATTER.match(text)
    if not m:
        return {}, text
    body = text[m.end() :]
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}, body
    return (data, body) if isinstance(data, dict) else ({}, body)


def derive_kind(path: str, front_matter: dict) -> str | None:
    fm = front_matter.get("kind")
    if isinstance(fm, str) and fm.strip():
        return fm.strip()
    parts = Path(path).parts
    try:
        i = parts.index(".engineering")
    except ValueError:
        return None
    rest = parts[i + 1 :]
    return rest[0] if len(rest) > 1 else None


def derive_title(path: str, front_matter: dict, body: str) -> str:
    fm = front_matter.get("title")
    if isinstance(fm, str) and fm.strip():
        return fm.strip()
    m = _ATX_HEADING.search(body)
    return m.group(1).strip() if m else Path(path).stem


def source_kind_for(path: str) -> str:
    """04 §6: everything under .engineering/sessions/ is a session summary."""
    return "session_summary" if path.startswith(f"{SESSIONS_DIR}/") else "artifact"


def _str_or_none(value) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def links_from_front_matter(fm: dict) -> list[LinkRow]:
    """Linkage rows described by a summary's front-matter (04 §6). One row per
    work item, plus one carrying the node/hook/session triple."""
    raw = fm.get("work_item_ids")
    if isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, list):
        raw = []
    links = [LinkRow(work_item_id=w) for w in (_str_or_none(x) for x in raw) if w]
    node = _str_or_none(fm.get("node_id"))
    hook = _str_or_none(fm.get("hook_point"))
    session = _str_or_none(fm.get("worker_session_id"))
    if node or hook or session:
        links.append(LinkRow(node_id=node, hook_point=hook, worker_session_id=session))
    return links


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _timestamps(repo: Path, rel: str) -> tuple[str | None, str | None]:
    # ponytail: two `git log` spawns per file. Fine for the small .engineering/
    # trees today; batch into one `git log --name-only` pass if a repo ever
    # carries hundreds of artifacts.
    updated = _git(repo, "log", "-1", "--format=%cI", "--", rel).stdout.splitlines()
    created = _git(
        repo, "log", "--diff-filter=A", "--follow", "--format=%cI", "--", rel
    ).stdout.splitlines()
    return (created[-1] if created else None, updated[0] if updated else None)


def scan_repo(repo: Path) -> list[ScannedDoc]:
    repo = Path(repo)
    listed = _git(repo, "ls-files", "-z", "--", ".engineering/")
    if listed.returncode != 0:
        return []
    rels = [r for r in listed.stdout.split("\0") if r.endswith(".md")]
    docs: list[ScannedDoc] = []
    for rel in rels:
        try:
            text = (repo / rel).read_text()
        except OSError:
            continue
        fm, body = split_front_matter(text)
        created, updated = _timestamps(repo, rel)
        source_kind = source_kind_for(rel)
        docs.append(
            ScannedDoc(
                path=rel,
                kind=derive_kind(rel, fm),
                title=derive_title(rel, fm, body),
                content=body,
                content_hash=hashlib.sha256(body.encode()).hexdigest(),
                metadata={k: v for k, v in fm.items() if k not in ("title", "kind")},
                source_created_at=created,
                source_updated_at=updated,
                source_kind=source_kind,
                # Every document, not only session summaries: a spec or plan an
                # agent wrote carries the same `work_item_ids:` key, and gating
                # on the source kind is what kept them out of `kraft view docs`
                # (Kraft-2k4). A document without the key still links to
                # nothing — `links_from_front_matter` returns an empty list.
                links=tuple(links_from_front_matter(fm)),
            )
        )
    return docs


def _meta_json(metadata: dict) -> str:
    # default=str: YAML front-matter can yield dates / other non-JSON scalars;
    # metadata_json is for display, so stringify rather than fail ingestion.
    return json.dumps(metadata, sort_keys=True, default=str)


def replace_links(conn, document_id: str, links) -> None:
    """Idempotent: a document's link set is rewritten whole, never appended to."""
    conn.execute("DELETE FROM document_links WHERE document_id=?", (document_id,))
    for link in links:
        conn.execute(
            "INSERT INTO document_links (id, document_id, work_item_id, node_id, "
            "hook_point, worker_session_id) VALUES (?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                document_id,
                link.work_item_id,
                link.node_id,
                link.hook_point,
                link.worker_session_id,
            ),
        )


def _drop_chunks(conn, document_id: str) -> None:
    """documents -> document_chunks cascades, but vec0 rows do not."""
    for r in conn.execute(
        "SELECT id FROM document_chunks WHERE document_id=?", (document_id,)
    ).fetchall():
        conn.execute("DELETE FROM document_vectors WHERE chunk_id=?", (r["id"],))
    conn.execute("DELETE FROM document_chunks WHERE document_id=?", (document_id,))


def _serialize(vector) -> bytes:
    """vec0 takes float32 little-endian blobs."""
    return struct.pack(f"<{len(vector)}f", *vector)


async def replace_chunks(conn, document_id: str, text: str, embedder=None) -> int:
    """Rewrite a document's chunks (and their vectors) whole. Returns the count.

    vec0 has no foreign keys, so its rows are deleted explicitly here rather
    than by cascade. An embedder that returns None (model missing or broken)
    leaves the chunks in place without vectors — losing text search because a
    model would not load is never the right trade (4C design §5).

    Async only for the encode step: `embedder.try_encode` is CPU-bound (model
    load + inference) and is run in a thread so a big scan's embedding pass
    doesn't starve the event loop the rest of the server answers requests on.
    `to_thread` on the plain method, not a dedicated async method on Embedder,
    so any duck-typed embedder (tests included) works without adding one.
    """
    old = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM document_chunks WHERE document_id=?", (document_id,)
        ).fetchall()
    ]
    for chunk_id in old:
        conn.execute("DELETE FROM document_vectors WHERE chunk_id=?", (chunk_id,))
    conn.execute("DELETE FROM document_chunks WHERE document_id=?", (document_id,))

    chunks = chunk_markdown(text)
    if not chunks:
        return 0
    ids = [uuid.uuid4().hex for _ in chunks]
    conn.executemany(
        "INSERT INTO document_chunks (id, document_id, chunk_index, chunk_text) VALUES (?,?,?,?)",
        [(cid, document_id, i, c) for i, (cid, c) in enumerate(zip(ids, chunks, strict=True))],
    )
    if embedder is not None:
        vectors = await asyncio.to_thread(embedder.try_encode, chunks)
        if vectors:
            conn.executemany(
                "INSERT INTO document_vectors (chunk_id, embedding) VALUES (?,?)",
                [(cid, _serialize(v)) for cid, v in zip(ids, vectors, strict=True)],
            )
    return len(chunks)


async def upsert_document(conn, repo: str, doc: ScannedDoc, now: str, embedder=None) -> str:
    """Insert or update one document keyed on (repo, path), replacing its links.
    Shared by the git scan and the event-driven summary path."""
    row = conn.execute(
        "SELECT id, content_hash FROM documents WHERE repo=? AND path=?", (repo, doc.path)
    ).fetchone()
    content_changed = row is None or row["content_hash"] != doc.content_hash
    if row is None:
        doc_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO documents (id, repo, source_kind, kind, title, path, content, "
            "content_hash, metadata_json, source_created_at, source_updated_at, indexed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                doc_id,
                repo,
                doc.source_kind,
                doc.kind,
                doc.title,
                doc.path,
                doc.content,
                doc.content_hash,
                _meta_json(doc.metadata),
                doc.source_created_at,
                doc.source_updated_at,
                now,
            ),
        )
    else:
        doc_id = row["id"]
        conn.execute(
            "UPDATE documents SET source_kind=?, kind=?, title=?, content=?, content_hash=?, "
            "metadata_json=?, source_created_at=?, source_updated_at=?, indexed_at=? WHERE id=?",
            (
                doc.source_kind,
                doc.kind,
                doc.title,
                doc.content,
                doc.content_hash,
                _meta_json(doc.metadata),
                doc.source_created_at,
                doc.source_updated_at,
                now,
                doc_id,
            ),
        )
    replace_links(conn, doc_id, doc.links)
    # Re-embedding unchanged text is the main avoidable cost in ingestion.
    if content_changed:
        await replace_chunks(conn, doc_id, doc.content, embedder)
    return doc_id


async def reconcile(
    conn,
    repo: str,
    scanned: list[ScannedDoc],
    *,
    now: str | None = None,
    embedder=None,
) -> ReconcileStats:
    now = now or _now()
    summaries = [d for d in scanned if d.source_kind == "session_summary"]
    scanned = [d for d in scanned if d.source_kind == "artifact"]
    source_kind = "artifact"
    existing = {
        r["path"]: (r["id"], r["content_hash"])
        for r in conn.execute(
            "SELECT id, path, content_hash FROM documents WHERE repo=? AND source_kind=?",
            (repo, source_kind),
        ).fetchall()
    }
    by_path = {d.path: d for d in scanned}

    new_paths = [p for p in by_path if p not in existing]
    gone_paths = [p for p in existing if p not in by_path]
    gone_by_hash = {existing[p][1]: p for p in gone_paths}

    ins = upd = ren = dele = 0
    conn.execute("BEGIN")
    try:
        # Renames first (and drop from new_paths): a UNIQUE(repo, path) row can
        # only move to a free path, so this must precede the inserts.
        consumed: set[str] = set()
        for p in list(new_paths):
            d = by_path[p]
            src = gone_by_hash.get(d.content_hash)
            if src is None or src in consumed:
                continue
            conn.execute(
                "UPDATE documents SET path=?, title=?, kind=?, metadata_json=?, "
                "source_created_at=?, source_updated_at=?, indexed_at=? WHERE id=?",
                (
                    d.path,
                    d.title,
                    d.kind,
                    _meta_json(d.metadata),
                    d.source_created_at,
                    d.source_updated_at,
                    now,
                    existing[src][0],
                ),
            )
            replace_links(conn, existing[src][0], d.links)
            consumed.add(src)
            new_paths.remove(p)
            ren += 1

        for p in new_paths:
            await upsert_document(conn, repo, by_path[p], now, embedder)
            ins += 1

        for src in gone_paths:
            if src in consumed:
                continue
            _drop_chunks(conn, existing[src][0])
            conn.execute("DELETE FROM documents WHERE id=?", (existing[src][0],))
            dele += 1

        for p, d in by_path.items():
            if p in existing and existing[p][1] != d.content_hash:
                conn.execute(
                    "UPDATE documents SET title=?, kind=?, content=?, content_hash=?, "
                    "metadata_json=?, source_created_at=?, source_updated_at=?, indexed_at=? "
                    "WHERE id=?",
                    (
                        d.title,
                        d.kind,
                        d.content,
                        d.content_hash,
                        _meta_json(d.metadata),
                        d.source_created_at,
                        d.source_updated_at,
                        now,
                        existing[p][0],
                    ),
                )
                replace_links(conn, existing[p][0], d.links)
                await replace_chunks(conn, existing[p][0], d.content, embedder)
                upd += 1

        # 04 §5 as amended by the 4B design: the summary bucket is upsert-only.
        # A live summary sits in a worktree, so every scan of the source repo
        # would otherwise "miss" it and delete it.
        for d in summaries:
            existed = conn.execute(
                "SELECT 1 FROM documents WHERE repo=? AND path=?", (repo, d.path)
            ).fetchone()
            await upsert_document(conn, repo, d, now, embedder)
            if existed is None:
                ins += 1
            else:
                upd += 1
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return ReconcileStats(inserted=ins, updated=upd, renamed=ren, deleted=dele)
