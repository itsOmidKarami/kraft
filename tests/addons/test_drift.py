"""The kraft-default drift check the `doctor` row and the server share."""

from __future__ import annotations

import pytest

from kraft.addons import drift
from kraft.addons import load as addons_load


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [([], [], True), (["x"], ["x"], True), (["x"], ["y"], False)],
    ids=["empty-is-none", "stable", "changes-with-a-line"],
)
def test_signature(a, b, same):
    assert (drift.signature(a) == drift.signature(b)) is same
    assert drift.signature([]) is None
    if a:
        assert len(drift.signature(a)) == 16


@pytest.mark.parametrize("how", ["writes-and-reads", "none-deletes", "malformed-reads-none"])
def test_marking(tmp_path, how):
    if how == "malformed-reads-none":
        (tmp_path / drift.NOTIFIED).write_text("{nope")
        assert drift.notified(tmp_path) is None
        return
    assert drift.notified(tmp_path) is None
    drift.mark_notified(tmp_path, "abc")
    assert drift.notified(tmp_path) == "abc"
    if how == "none-deletes":
        drift.mark_notified(tmp_path, None)
        assert not (tmp_path / drift.NOTIFIED).exists()
        assert drift.notified(tmp_path) is None


def test_a_check_that_raises_is_one_line(tmp_path, monkeypatch):
    """The row at base said only this line when the read failed, whatever the
    update record had already added; the lines gathered so far are dropped."""

    def boom(*a, **k):
        raise RuntimeError("bad")

    monkeypatch.setattr(addons_load, "installed", boom)
    (tmp_path / "chains").mkdir()
    addons = tmp_path / "addons"
    addons.mkdir()
    (addons / "builtin.json").write_text('{"to": "9.9.9", "status": "held", "lines": "oops"}')
    assert drift.lines(tmp_path, addons) == ["cannot read kraft-default: bad"]
