"""Password and session handling for off-localhost access (design 1m / 5e).

Kraft is a single-operator tool on a LAN. The rule is the one the Access screen
states: **auth is off on localhost and on for anything else.** Binding to
0.0.0.0 without a password is refused rather than quietly allowed, because that
is the configuration that puts an agent runner on the office wifi.

Storage:

* the password is kept as a scrypt hash with a per-password salt, never in
  plaintext and never reversible;
* the session cookie carries a 256-bit random token, and only its SHA-256 is
  stored — a leaked database cannot be replayed as a login.

Both comparisons are constant-time.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

COOKIE = "kraft_session"

# scrypt parameters: interactive-login cost, ~16MB of memory per hash.
_N, _R, _P = 2**14, 8, 1
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """`scrypt$<hex salt>$<hex key>` — self-describing, so parameters can change."""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, salt_hex, key_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=_N, r=_R, p=_P)
    except ValueError, TypeError:
        return False
    return hmac.compare_digest(key.hex(), key_hex)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_id(token: str) -> str:
    """The stored form of a token. Never store the token itself."""
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def create_session(
    conn: sqlite3.Connection, token: str, *, label: str, ip: str, expiry_days: int
) -> None:
    now = _now()
    conn.execute(
        "INSERT OR REPLACE INTO auth_sessions "
        "(id, label, ip, created_at, last_seen_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            token_id(token),
            label,
            ip,
            now.isoformat(),
            now.isoformat(),
            (now + timedelta(days=expiry_days)).isoformat(),
        ),
    )


def touch_session(conn: sqlite3.Connection, token: str) -> bool:
    """True if the token names a live session, refreshing its last-seen stamp."""
    now = _now()
    row = conn.execute(
        "SELECT expires_at FROM auth_sessions WHERE id = ?", (token_id(token),)
    ).fetchone()
    if row is None:
        return False
    try:
        if datetime.fromisoformat(row["expires_at"]) <= now:
            conn.execute("DELETE FROM auth_sessions WHERE id = ?", (token_id(token),))
            return False
    except ValueError:
        return False
    conn.execute(
        "UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?",
        (now.isoformat(), token_id(token)),
    )
    return True


def list_sessions(conn: sqlite3.Connection, current: str | None = None) -> list[dict]:
    rows = conn.execute("SELECT * FROM auth_sessions ORDER BY last_seen_at DESC").fetchall()
    cur = token_id(current) if current else None
    return [
        {
            "id": r["id"],
            "label": r["label"],
            "ip": r["ip"],
            "created_at": r["created_at"],
            "last_seen_at": r["last_seen_at"],
            "expires_at": r["expires_at"],
            "current": r["id"] == cur,
        }
        for r in rows
    ]


def revoke_session(conn: sqlite3.Connection, session_id: str) -> int:
    cur = conn.execute("DELETE FROM auth_sessions WHERE id = ?", (session_id,))
    return cur.rowcount


def revoke_all(conn: sqlite3.Connection) -> int:
    """Used when the password changes: every old session dies with it."""
    return conn.execute("DELETE FROM auth_sessions").rowcount


MCP_TOKEN_FILE = "mcp-token"


def read_mcp_token(run_dir: str | Path) -> str | None:
    """The token on disk, or None. Whitespace-only counts as absent."""
    try:
        token = (Path(run_dir) / MCP_TOKEN_FILE).read_text().strip()
    except OSError:
        return None
    return token or None


def ensure_mcp_token(run_dir: str | Path) -> str:
    """The bearer credential for non-browser clients (design §5).

    Created once and kept: regenerating per serve would silently break an MCP
    client registered against the old value. Opened 0600 rather than chmod'd
    after the write, so the secret is never briefly world-readable.
    """
    existing = read_mcp_token(run_dir)
    if existing:
        return existing
    token = new_token()
    path = Path(run_dir) / MCP_TOKEN_FILE
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(token)
    return token
