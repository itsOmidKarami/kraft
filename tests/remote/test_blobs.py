"""Large payloads cross a connection as ordered `blob` frames."""

from __future__ import annotations

import os

import pytest
from support.remote import memory_pair

from kraft.remote import blobs, protocol


@pytest.mark.parametrize(
    "size",
    [0, 1, blobs.CHUNK, blobs.CHUNK + 1, 3 * blobs.CHUNK + 5],
    ids=["empty", "one-byte", "exactly-a-chunk", "a-chunk-and-a-byte", "three-chunks"],
)
async def test_a_file_crosses_as_ordered_chunks_and_reassembles_byte_for_byte(tmp_path, size):
    source = tmp_path / "src.bin"
    data = os.urandom(size)
    source.write_bytes(data)
    a, b = memory_pair()
    await blobs.send_blob(a, job="w1/s1", name="code", source=source)
    sink, done = blobs.BlobSink(tmp_path / "in"), None
    while done is None:
        done = sink.add(await b.recv())
    assert done.read_bytes() == data


_OK = {"t": "blob", "id": "w/s", "name": "x", "seq": 0, "last": False, "data": ""}


@pytest.mark.parametrize(
    ("frames", "says"),
    [
        ([{"seq": 0}, {"seq": 2, "last": True}], "out of order"),
        ([{"seq": -1}], "bad blob frame"),
        ([{"seq": "0"}], "bad blob frame"),
        ([{"last": 1}], "bad blob frame"),
        ([{"data": 7}], "bad blob frame"),
        ([{"data": "!!!"}], "not base64"),
    ],
    ids=[
        "out-of-order",
        "negative-seq",
        "seq-not-an-int",
        "last-not-a-bool",
        "data-not-a-str",
        "not-base64",
    ],
)
async def test_a_bad_blob_frame_ends_the_connection(tmp_path, frames, says):
    sink = blobs.BlobSink(tmp_path)
    with pytest.raises(protocol.Closed, match=says):
        for patch in frames:
            sink.add({**_OK, **patch})


async def test_a_blob_over_the_cap_is_dropped_and_ends_the_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(blobs, "MAX_BLOB_BYTES", 3)
    sink = blobs.BlobSink(tmp_path)
    sink.add({**_OK, "data": "YWJj"})  # "abc": exactly the cap
    with pytest.raises(protocol.Closed, match="over"):
        sink.add({**_OK, "seq": 1, "data": "ZA=="})
    assert not sink.path_of("w/s", "x").exists()


async def test_a_name_with_a_slash_cannot_leave_the_directory(tmp_path):
    sink = blobs.BlobSink(tmp_path / "in")
    done = sink.add(
        {"t": "blob", "id": "../../etc", "name": "../x", "seq": 0, "last": True, "data": ""}
    )
    assert done.parent == tmp_path / "in"
