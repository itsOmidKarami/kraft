from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

from kraft.events import _now

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_ATX_HEADING = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


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
            )
        )
    return docs


def _meta_json(metadata: dict) -> str:
    # default=str: YAML front-matter can yield dates / other non-JSON scalars;
    # metadata_json is for display, so stringify rather than fail ingestion.
    return json.dumps(metadata, sort_keys=True, default=str)


def reconcile(
    conn,
    repo: str,
    scanned: list[ScannedDoc],
    *,
    source_kind: str = "artifact",
    now: str | None = None,
) -> ReconcileStats:
    now = now or _now()
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
            consumed.add(src)
            new_paths.remove(p)
            ren += 1

        for p in new_paths:
            d = by_path[p]
            conn.execute(
                "INSERT INTO documents (id, repo, source_kind, kind, title, path, content, "
                "content_hash, metadata_json, source_created_at, source_updated_at, indexed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    repo,
                    source_kind,
                    d.kind,
                    d.title,
                    d.path,
                    d.content,
                    d.content_hash,
                    _meta_json(d.metadata),
                    d.source_created_at,
                    d.source_updated_at,
                    now,
                ),
            )
            ins += 1

        for src in gone_paths:
            if src in consumed:
                continue
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
                upd += 1
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return ReconcileStats(inserted=ins, updated=upd, renamed=ren, deleted=dele)
