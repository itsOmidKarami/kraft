"""Large payloads (a git bundle, a tar of files) as ordered `blob` frames, so a
frame never exceeds `transport.FRAME_LIMIT` and a payload is never held in memory."""

from __future__ import annotations

import base64
import re
from pathlib import Path

from kraft.remote.protocol import Closed, Transport

CHUNK = 1 << 20
#: One blob from a machine is untrusted: it may not fill the server's disk.
MAX_BLOB_BYTES = 4 << 30
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def safe(name: str) -> str:
    """`name` as part of a file name."""
    return _SAFE.sub("_", name)


async def send_blob(t: Transport, *, job: str, name: str, source: Path) -> None:
    size, sent, seq = source.stat().st_size, 0, 0
    with open(source, "rb") as fh:
        while True:
            data = fh.read(CHUNK)
            sent += len(data)
            last = sent >= size
            await t.send(
                {
                    "t": "blob",
                    "id": job,
                    "name": name,
                    "seq": seq,
                    "last": last,
                    "data": base64.b64encode(data).decode("ascii"),
                }
            )
            seq += 1
            if last:
                return


class BlobSink:
    """Reassembles `blob` frames into files under `directory`, one per (job, name).
    A chunk with `seq == 0` starts the file again, so a resend after a reconnect replaces it."""

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._open: dict[tuple[str, str], int] = {}

    def path_of(self, job: str, name: str) -> Path:
        return self._dir / f"{safe(job)}.{safe(name)}"

    def add(self, frame: dict) -> Path | None:
        seq, last, data = frame.get("seq"), frame.get("last"), frame.get("data")
        if not (isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0):
            raise Closed("bad blob frame")
        if not isinstance(last, bool) or not isinstance(data, str):
            raise Closed("bad blob frame")
        key = (str(frame["id"]), str(frame["name"]))
        path = self.path_of(*key)
        if seq == 0:
            path.write_bytes(b"")
            self._open[key] = 0
        elif self._open.get(key) != seq - 1:
            raise Closed(f"blob {key[1]} of {key[0]} out of order at chunk {seq}")
        try:
            chunk = base64.b64decode(data, validate=True)
        except ValueError as exc:
            raise Closed("blob chunk is not base64") from exc
        with open(path, "ab") as fh:
            fh.write(chunk)
        if path.stat().st_size > MAX_BLOB_BYTES:
            path.unlink(missing_ok=True)
            self._open.pop(key, None)
            raise Closed(f"blob {key[1]} of {key[0]} is over {MAX_BLOB_BYTES >> 30} GiB")
        self._open[key] = seq
        if last:
            del self._open[key]
            return path
        return None
