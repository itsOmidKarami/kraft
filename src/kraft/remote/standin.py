"""The step's process on the server: `kraft admin worker attach`. `run_task` starts it where it
would start the agent, and everything `run_task` knows how to do with a child (tail its log for
usage, signal it, wait for it, read its exit code, adopt it by pid after a restart) now works for
a step that runs on another machine."""

from __future__ import annotations

import asyncio
import base64
import os
import signal
import stat
import sys
from pathlib import Path

from kraft.remote import protocol
from kraft.remote.transport import FRAME_LIMIT, StreamTransport

KEY_FILE, PORT_FILE = "attach.key", "attach.port"


def state_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "remote"


def argv(run_dir: Path, work_item_id: str, session_id: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "kraft",
        "admin",
        "worker",
        "attach",
        "--run-dir",
        str(run_dir),
        "--item",
        work_item_id,
        "--session",
        session_id,
    ]


def _write_all(fd: int, data: bytes) -> None:
    while data:
        data = data[os.write(fd, data) :]


async def attach(
    run_dir: Path,
    work_item_id: str,
    session_id: str,
    *,
    out_fd: int = 1,
    retry_s: float = 0.2,
    kill_exit_s: float = 20.0,
) -> int:
    loop = asyncio.get_running_loop()
    pending: asyncio.Queue[str] = asyncio.Queue()
    written = 0

    def on_signal(name: str) -> None:
        pending.put_nowait(name)
        if name == "KILL":
            loop.call_later(kill_exit_s, os._exit, 143)

    loop.add_signal_handler(signal.SIGINT, on_signal, "INT")
    loop.add_signal_handler(signal.SIGTERM, on_signal, "KILL")

    def offset() -> int:
        st = os.fstat(out_fd)
        return st.st_size if stat.S_ISREG(st.st_mode) else written

    async def forward(t) -> None:
        while True:
            name = await pending.get()
            try:
                await t.send({"t": "signal", "sig": name})
            except protocol.Closed:
                pending.put_nowait(name)  # no connection to carry it: the next one will
                return

    while True:
        try:
            port = int((state_dir(run_dir) / PORT_FILE).read_text())
            key = (state_dir(run_dir) / KEY_FILE).read_text().strip()
            reader, writer = await asyncio.open_connection("127.0.0.1", port, limit=FRAME_LIMIT)
        except (OSError, ValueError):
            await asyncio.sleep(retry_s)
            continue
        t = StreamTransport(reader, writer)
        sender = None
        try:
            await t.send(
                {
                    "t": "attach",
                    "key": key,
                    "item": work_item_id,
                    "session": session_id,
                    "offset": offset(),
                }
            )
            sender = asyncio.create_task(forward(t))
            while True:
                frame = await t.recv()
                kind = frame.get("t")
                if kind == "log":
                    data = base64.b64decode(frame["data"])
                    _write_all(out_fd, data)
                    written += len(data)
                elif kind == "exit":
                    return int(frame["code"])
                elif kind == "gone":
                    if frame.get("reason") == "refused":
                        raise protocol.Closed(
                            "refused"
                        )  # a key that rotated under us: read it again
                    _write_all(out_fd, f"kraft: {frame.get('reason')}\n".encode())
                    return 1
        except (protocol.Closed, OSError):
            pass
        finally:
            if sender is not None:
                sender.cancel()
            await t.close()
        await asyncio.sleep(retry_s)
