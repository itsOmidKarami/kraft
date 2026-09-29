"""`kraft.worker.channel`: one unix-socket listener per session under
`run/sn/<short>/s.sock`, serving `egress.EgressProxy` on the daemon's loop."""

from __future__ import annotations

import asyncio
import shutil
import stat
import tempfile
from pathlib import Path

import pytest

from kraft import events, store
from kraft.paths import RunDirs
from kraft.worker import channel, egress
from kraft.worker.sandbox import SandboxNotReady

_DENY_ALL = egress.PhaseLists("runtime", (), ("**",))


@pytest.fixture
def short_run():
    """A run dir short enough for a socket path: pytest's own tmp_path on
    macOS alone is past the 104 bytes a unix socket path may be."""
    base = Path(tempfile.mkdtemp(prefix="kraft-sn-", dir="/tmp"))
    yield RunDirs(base).ensure()
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
async def registry(database, short_run):
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id="B",
            title="t",
            repo="/r",
            chain_template="quick-task",
            chain_definition="{}",
        )
    )
    reg = channel.ChannelRegistry(short_run, database)
    yield reg
    await reg.close_all()


async def _ask(path, request: bytes) -> bytes:
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write(request)
    answer = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    return answer


async def test_a_sessions_socket_serves_the_proxy_and_records_against_its_item(
    registry, database, short_run
):
    path = await registry.open("0123456789abcdef0123", "w1", _DENY_ALL)
    assert path == short_run.base / "sn" / "0123456789abcdef" / "s.sock"
    # Only the daemon's user may reach a session's socket, or its directory.
    assert [stat.S_IMODE(d.stat().st_mode) for d in (short_run.sockets, path.parent)] == [
        0o700,
        0o700,
    ]

    answer = await _ask(path, b"CONNECT api.example.com:443 HTTP/1.1\r\n\r\n")

    assert answer.startswith(b"HTTP/1.1 403 ")
    rows = database.read(lambda c: events.read_after(c, 0, "w1"))
    refused = [r for r in rows if r["type"] == egress.SANDBOX_EGRESS_REFUSED]
    assert [r["payload"]["session_id"] for r in refused] == ["0123456789abcdef0123"]


async def test_close_stops_the_listener_and_removes_its_directory(registry):
    path = await registry.open("0123456789abcdef0123", "w1", _DENY_ALL)
    await registry.close("0123456789abcdef0123")

    assert not path.parent.exists()
    with pytest.raises(OSError):
        await asyncio.open_unix_connection(str(path))
    # Closing what is not open is a no-op, as every teardown path expects.
    await registry.close("0123456789abcdef0123")


async def test_shutdown_keeps_the_directory_a_live_relay_mounts(registry):
    """A graceful restart: the relay bind-mounts the socket's directory, so a
    new directory (a new inode) would cut an adopted session off for good.
    Only the socket goes; the next `open` listens in the same directory."""
    path = await registry.open("0123456789abcdef0123", "w1", _DENY_ALL)
    inode = path.parent.stat().st_ino
    await registry.close_all()

    assert path.parent.stat().st_ino == inode and not path.exists()
    assert await registry.open("0123456789abcdef0123", "w1", _DENY_ALL) == path
    assert path.parent.stat().st_ino == inode
    assert (await _ask(path, b"CONNECT a.io:443 HTTP/1.1\r\n\r\n")).startswith(b"HTTP/1.1 403 ")


async def test_a_socket_path_too_long_for_a_unix_socket_is_refused_by_name(database, tmp_path):
    deep = RunDirs(tmp_path / ("d" * 100)).ensure()
    reg = channel.ChannelRegistry(deep, database)
    with pytest.raises(SandboxNotReady, match="too long for a unix socket"):
        await reg.open("0123456789abcdef0123", "w1", _DENY_ALL)
