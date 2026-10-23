"""The files that travel with a step, and the rules for what comes back."""

import io
import tarfile

import pytest

from kraft.remote import files


def _tree(tmp_path):
    docs = tmp_path / "run/items/w1/docs"
    out = tmp_path / "run/items/w1/sessions/s1/out"
    prev = tmp_path / "run/items/w1/sessions/s0/out/result.json"
    for p, t in (
        (docs / "spec.md", "spec v1"),
        (out / "result.json", ""),
        (prev, '{"status":"done"}'),
    ):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(t)
    items = files.entries(results=[prev], ro=[], rw_dirs=[docs, out], rw_files=[])
    return docs, out, prev, items


def test_a_round_trip_returns_only_what_the_step_changed_or_added(tmp_path):
    docs, out, prev, items = _tree(tmp_path)
    pushed = files.pack(items, tmp_path / "files.tgz")
    machine = tmp_path / "machine"

    def mapper(name):  # the machine keeps them elsewhere
        return machine / name

    files.unpack(tmp_path / "files.tgz", mapper)
    (mapper(str(docs).lstrip("/")) / "spec.md").write_text("spec v2")
    (mapper(str(docs).lstrip("/")) / "plan.md").write_text("plan")
    (mapper(str(out).lstrip("/")) / "result.json").write_text('{"status":"done"}')
    assert files.changed(items, mapper, pushed, tmp_path / "back.tgz")
    got = files.apply_returned(tmp_path / "back.tgz", items, pushed, tmp_path / "kept")
    assert (docs / "spec.md").read_text() == "spec v2" and (docs / "plan.md").read_text() == "plan"
    assert (out / "result.json").read_text() == '{"status":"done"}' and not got.conflicts
    assert prev.read_text() == '{"status":"done"}'  # a read-only entry is never written back


def test_a_document_a_person_edited_meanwhile_keeps_the_servers_version_and_saves_the_steps(
    tmp_path,
):
    docs, out, prev, items = _tree(tmp_path)
    pushed = files.pack(items, tmp_path / "files.tgz")

    def mapper(name):
        return tmp_path / "m" / name

    files.unpack(tmp_path / "files.tgz", mapper)
    (mapper(str(docs).lstrip("/")) / "spec.md").write_text("the step's edit")
    files.changed(items, mapper, pushed, tmp_path / "back.tgz")
    (docs / "spec.md").write_text("a person's edit")  # the server's copy moved while the step ran
    got = files.apply_returned(tmp_path / "back.tgz", items, pushed, tmp_path / "kept")
    assert (docs / "spec.md").read_text() == "a person's edit"
    assert got.conflicts == [docs / "spec.md"]
    assert (tmp_path / "kept" / "spec.md").read_text() == "the step's edit"


def _archive(path, *names):
    with tarfile.open(path, "w:gz") as tar:
        for name in names:
            info = tarfile.TarInfo(name)
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
    return path


@pytest.mark.parametrize(
    "name",
    ["../escape", "etc/passwd", "tmp/elsewhere/x"],
    ids=["dotdot", "absolute-elsewhere", "not-an-entry"],
)
def test_a_returned_archive_cannot_write_outside_the_read_write_entries(tmp_path, name):
    docs, out, prev, items = _tree(tmp_path)
    archive = _archive(tmp_path / "evil.tgz", name)
    got = files.apply_returned(archive, items, {}, tmp_path / "kept")
    assert got.written == [] and not (tmp_path / "escape").exists()


def test_a_returned_archive_over_the_file_count_is_refused_and_writes_nothing(
    tmp_path, monkeypatch
):
    docs, out, prev, items = _tree(tmp_path)
    inside = str(docs).lstrip("/")
    archive = _archive(tmp_path / "many.tgz", f"{inside}/a.md", f"{inside}/b.md", f"{inside}/c.md")
    monkeypatch.setattr(files, "MAX_FILES", 2)
    with pytest.raises(files.FilesRefused, match="what the step returned"):
        files.apply_returned(archive, items, {}, tmp_path / "kept")
    assert not (docs / "a.md").exists()


def test_a_co_task_leaves_files_a_sibling_has_already_changed(tmp_path):
    docs, out, prev, items = _tree(tmp_path)
    pushed = files.pack(items, tmp_path / "files.tgz")

    def mapper(name):
        return tmp_path / "m" / name

    files.unpack(tmp_path / "files.tgz", mapper)
    mine = mapper(str(docs).lstrip("/")) / "spec.md"
    mine.write_text("a sibling's edit")
    again = files.unpack(tmp_path / "files.tgz", mapper, keep_existing=True)
    assert mine.read_text() == "a sibling's edit" and again == pushed


def test_a_symlink_is_not_packed(tmp_path):
    docs, out, prev, items = _tree(tmp_path)
    (docs / "link").symlink_to("/etc/passwd")
    pushed = files.pack(items, tmp_path / "f.tgz")
    assert not any(name.endswith("/link") for name in pushed)


def test_what_the_step_needs_over_the_cap_is_refused(tmp_path, monkeypatch):
    docs, out, prev, items = _tree(tmp_path)
    monkeypatch.setattr(files, "MAX_FILES", 1)
    with pytest.raises(files.FilesRefused, match="trim the item's docs"):
        files.pack(items, tmp_path / "f.tgz")
