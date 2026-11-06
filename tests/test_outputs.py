"""Step outputs: what a capture checks, what it keeps, and what it never lets
a worker's choice of name or value reach."""

from __future__ import annotations

import dataclasses
import errno
import os

import pytest
from support.harness import own_files, write

from kraft import item_paths, outputs

WID = "w1"
MARKER = "ZZWORKERZZ"


def launched(run_dirs, *, files=("a.txt",), variables=(), cap=1 << 20, writer="n.s.t", sid="s1"):
    """A session that declared outputs: its files, and the folder it writes."""
    session = own_files(run_dirs, sid, WID)
    out = outputs.declare(session, outputs.Decl(writer, tuple(files), tuple(variables), cap))
    return session, out


def snapshot(run_dirs, writer="n.s.t"):
    return item_paths.snapshot_dir(run_dirs, WID, writer)


def temp_left(run_dirs):
    folder = item_paths.outputs_dir(run_dirs, WID)
    return [p.name for p in folder.glob(".*")] if folder.is_dir() else []


def sparse(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    os.truncate(path, size)


@pytest.mark.parametrize(
    ("fill", "why"),
    [
        (lambda out: sparse(out / f"{MARKER}.bin", 2 << 20), "limit"),
        (
            lambda out: [write(out, f"{MARKER}{i}", "x") for i in range(101)],
            "more than 100 entries",
        ),
        (lambda out: write(out, "variables.env", f"{MARKER}=1\n"), "PIPELINE_ID is missing"),
        (
            lambda out: write(out, "variables.env", f"PIPELINE_ID={MARKER}{'x' * 4100}\n"),
            "PIPELINE_ID: value is over 4096 bytes",
        ),
        (
            lambda out: write(out, "variables.env", f"PIPELINE_ID={MARKER}\x1b[31m\n"),
            "PIPELINE_ID: value holds a control character",
        ),
        (
            lambda out: write(out, "variables.env", f"PIPELINE_ID={MARKER}\x00\n"),
            "PIPELINE_ID: value holds a control character",
        ),
        (
            lambda out: write(
                out,
                "variables.env",
                "PIPELINE_ID=1\n" + "".join(f"{MARKER}{i}=1\n" for i in range(50)),
            ),
            "more than 50 variables",
        ),
        (lambda out: write(out, "a.txt", "fine"), "PIPELINE_ID is missing"),
    ],
    ids=[
        "total-size-over-the-cap",
        "101-entries",
        "declared-variable-missing",
        "value-of-4097-bytes",
        "escape-in-a-value",
        "nul-in-a-value",
        "51-variables",
        "files-fine-but-a-variable-fails",
    ],
)
def test_capture_fails(run_dirs, fill, why):
    session, out = launched(run_dirs, variables=["PIPELINE_ID"])
    fill(out)

    message = outputs.capture(run_dirs, WID, session)

    assert why in message
    assert MARKER not in message
    assert MARKER not in session.log.read_text()
    assert not snapshot(run_dirs).exists()
    assert temp_left(run_dirs) == []
    assert not out.exists()


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "fifo"])
def test_capture_skips_unsafe_files(run_dirs, unsafe):
    session, out = launched(run_dirs, files=["a.txt", "b.txt"])
    write(out, "a.txt", "kept")
    if unsafe == "symlink":
        (out / "b.txt").symlink_to(out / "a.txt")
    elif unsafe == "hardlink":
        write(out, "other", "shared")
        os.link(out / "other", out / "b.txt")
    else:
        os.mkfifo(out / "b.txt")

    assert outputs.capture(run_dirs, WID, session) is None

    assert sorted(p.name for p in snapshot(run_dirs).iterdir()) == ["a.txt", "variables.env"]
    log = session.log.read_text()
    assert "kept 1 file(s), 1 missing" in log
    assert "b.txt" not in log


@pytest.mark.parametrize(
    ("name", "env", "want"),
    [
        ("report.xml", "PIPELINE_ID=7\n", b"PIPELINE_ID=7\n"),
        ("report final.xml", "PIPELINE_ID=7\n", b"PIPELINE_ID=7\n"),
        ("résumé.txt", "PIPELINE_ID=7\n", b"PIPELINE_ID=7\n"),
        ("report.xml", "# note\r\nOTHER=1\r\nPIPELINE_ID=7\r\n", b"PIPELINE_ID=7\n"),
    ],
    ids=["plain", "space-in-name", "non-ascii", "crlf"],
)
def test_capture_keeps(run_dirs, name, env, want):
    session, out = launched(run_dirs, files=[name], variables=["PIPELINE_ID"])
    write(out, name, "body")
    (out / "variables.env").write_bytes(env.encode())
    write(out, "undeclared.txt", "left behind")

    assert outputs.capture(run_dirs, WID, session) is None

    kept = snapshot(run_dirs)
    assert (kept / name).read_text() == "body"
    assert (kept / "variables.env").read_bytes() == want
    assert not (kept / "undeclared.txt").exists()
    assert "kept 1 file(s), 0 missing; variables PIPELINE_ID" in session.log.read_text()


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (b"# c\n\nA=1\n", {"A": b"1"}),
        (b"A=1=2\n", {"A": b"1=2"}),
        (b"A=1\nA=2\n", {"A": b"2"}),
        (b"A=\"x y\"\nB='z'\n", {"A": b'"x y"', "B": b"'z'"}),
        (b"A=1\r\nB=2\r\n", {"A": b"1", "B": b"2"}),
        (b"no equals here\n", {}),
    ],
    ids=[
        "comments-and-blanks",
        "equals-in-value",
        "last-wins",
        "no-quote-stripping",
        "crlf",
        "no-equals",
    ],
)
def test_variables_env_parsing(text, want):
    assert outputs.parse_variables(text) == want


def test_capture_without_sidecar_is_none(run_dirs):
    out = item_paths.writer_dir(own_files(run_dirs, "s1", WID))
    write(out, "a.txt", "mine")

    assert outputs.capture(run_dirs, WID, own_files(run_dirs, "s1", WID)) is None

    assert (out / "a.txt").read_text() == "mine"


def test_capture_replaces_previous_snapshot(run_dirs):
    first, out = launched(run_dirs, files=["a.txt", "b.txt"], sid="s1")
    write(out, "a.txt", "old")
    write(out, "b.txt", "old")
    assert outputs.capture(run_dirs, WID, first) is None
    second, out = launched(run_dirs, files=["a.txt"], sid="s2")
    write(out, "a.txt", "new")

    assert outputs.capture(run_dirs, WID, second) is None

    assert sorted(p.name for p in snapshot(run_dirs).iterdir()) == ["a.txt", "variables.env"]
    assert (snapshot(run_dirs) / "a.txt").read_text() == "new"


@pytest.mark.parametrize("fails", [False, True], ids=["kept", "failed"])
def test_a_session_captured_twice_keeps_what_the_first_capture_concluded(run_dirs, fails):
    """The crash fell between dropping the folder and recording the session: the
    second capture finds no folder, and is not a capture of nothing."""
    session, out = launched(run_dirs)
    if fails:
        sparse(out / "big", 2 << 20)
    else:
        write(out, "a.txt", "good")
    first = outputs.capture(run_dirs, WID, session)
    assert (first is not None) is fails

    assert outputs.capture(run_dirs, WID, session) == first

    assert snapshot(run_dirs).exists() is not fails
    if not fails:
        assert (snapshot(run_dirs) / "a.txt").read_text() == "good"


@pytest.mark.parametrize("how", ["check-fails", "walk-raises", "snapshot-stays"])
def test_a_failed_capture_leaves_no_snapshot_of_an_earlier_run(run_dirs, monkeypatch, how):
    first, out = launched(run_dirs, sid="s1")
    write(out, "a.txt", "old")
    assert outputs.capture(run_dirs, WID, first) is None
    second, out = launched(run_dirs, sid="s2")
    write(out, "a.txt", "new")
    if how == "walk-raises":

        def walk(*_a):
            raise OSError(errno.EMFILE, "too many open files")

        monkeypatch.setattr(outputs, "_check_folder", walk)
    else:
        sparse(out / "big", 2 << 20)
    if how == "snapshot-stays":
        monkeypatch.setattr(item_paths, "remove_tree", lambda _path: None)

    message = outputs.capture(run_dirs, WID, second)

    assert message is not None
    if how != "check-fails":
        assert message == "kraft: outputs: could not store the declared outputs"
    if how != "snapshot-stays":
        assert not snapshot(run_dirs).exists()


def test_capture_follows_no_link_to_the_output_folder(run_dirs, tmp_path):
    """A worker swapping `out/` for a link takes nothing from outside."""
    session, _ = launched(run_dirs, variables=["PIPELINE_ID"])
    victim = tmp_path / "victim"
    write(victim / "outputs", "a.txt", "precious")
    write(victim / "outputs", "variables.env", "PIPELINE_ID=1\n")
    os.rename(session.folder / "out", session.folder / "out.real")
    (session.folder / "out").symlink_to(victim)

    message = outputs.capture(run_dirs, WID, session)

    assert "PIPELINE_ID is missing" in message
    assert (victim / "outputs" / "a.txt").read_text() == "precious"
    assert not snapshot(run_dirs).exists()


@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("K", "later"),
        ("ONLY_FIRST", "first"),
        ("UNDECLARED", None),
        ("GONE", None),
    ],
    ids=["later-writer-wins", "found-in-an-earlier-writer", "undeclared-key", "absent-snapshot"],
)
def test_lookup_variable(run_dirs, name, want):
    for sid, writer, env in [
        ("s1", "n.s.a", "K=first\nONLY_FIRST=first\nUNDECLARED=x\n"),
        ("s2", "n.s.b", "K=later\n"),
    ]:
        session, out = launched(
            run_dirs,
            files=[],
            variables=["K", "ONLY_FIRST"][: 2 if writer == "n.s.a" else 1],
            writer=writer,
            sid=sid,
        )
        write(out, "variables.env", env)
        assert outputs.capture(run_dirs, WID, session) is None
    declared = {"n.s.a": ["K", "ONLY_FIRST"], "n.s.b": ["K"], "n.s.c": ["GONE"]}

    found = outputs.lookup_variable(run_dirs, WID, ["n.s.a", "n.s.b", "n.s.c"], name, declared)

    assert found == want


def test_lookup_variable_reads_the_current_declaration_not_the_old_snapshot(run_dirs):
    """A retry whose new chain dropped a variable: the file may still hold it."""
    session, out = launched(run_dirs, files=[], variables=["OLD"], writer="n.s.a")
    write(out, "variables.env", "OLD=1\n")
    assert outputs.capture(run_dirs, WID, session) is None

    assert outputs.lookup_variable(run_dirs, WID, ["n.s.a"], "OLD", {"n.s.a": ["OLD"]}) == "1"
    assert outputs.lookup_variable(run_dirs, WID, ["n.s.a"], "OLD", {"n.s.a": ["NEW"]}) is None


def test_carry_over_holds_the_whole_folder_to_the_cap(run_dirs):
    old, out = launched(run_dirs, sid="s1")
    for name in "abc":
        write(out, name, "x" * 400)
    new, _ = launched(run_dirs, sid="s2", cap=1000)

    outputs.carry_over(old, new, outputs.Decl("n.s.t", (), (), 1000))

    carried = sorted(p.name for p in item_paths.writer_dir(new).iterdir())
    assert len(carried) == 2, "the third would take it past 1000 bytes"


def test_writer_dir_ignores_writable(run_dirs):
    """An escalation thread's turns share one `writable`; the output folder is
    still the session's own."""
    session = own_files(run_dirs, "s1", WID)
    thread = dataclasses.replace(session, writable=item_paths.thread_out(run_dirs, WID, 4))

    assert item_paths.writer_dir(thread) == session.folder / "out" / "outputs"


@pytest.mark.parametrize(
    ("size", "cap", "trimmed"),
    [(10, 1000, False), (5000, 1000, True)],
    ids=["within-the-cap", "over-the-cap"],
)
def test_trim_oversized_drops_only_a_folder_over_the_cap(run_dirs, size, cap, trimmed):
    session, out = launched(run_dirs)
    write(out, "a.txt", "x" * size)

    assert outputs.trim_oversized(session, cap) is trimmed

    assert out.exists() is not trimmed


def test_trim_oversized_counts_entries_too(run_dirs):
    session, out = launched(run_dirs)
    for i in range(outputs.MAX_ENTRIES + 1):
        write(out, f"f{i}", "x")

    assert outputs.trim_oversized(session, 1 << 20) is True
    assert not out.exists()


def test_refuse_deletes_the_folder_and_logs_why_only_for_a_writer(run_dirs):
    session, out = launched(run_dirs)
    write(out, "a.txt", "x")

    assert outputs.refuse(run_dirs, WID, session, "kraft: outputs: because") == (
        "kraft: outputs: because"
    )

    assert not out.exists()
    assert "because" in session.log.read_text()
    other = own_files(run_dirs, "s2", WID)
    assert outputs.refuse(run_dirs, WID, other, "kraft: outputs: because") is None


def test_refuse_leaves_no_snapshot_of_an_earlier_run(run_dirs):
    first, out = launched(run_dirs, sid="s1")
    write(out, "a.txt", "old")
    assert outputs.capture(run_dirs, WID, first) is None
    second, _ = launched(run_dirs, sid="s2")

    outputs.refuse(run_dirs, WID, second, "kraft: outputs: because")

    assert not snapshot(run_dirs).exists()
