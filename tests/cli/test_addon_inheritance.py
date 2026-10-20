"""`kraft admin addon enable` for an add-on that builds on another: the
child is enabled only while its parent loads and accepts it. The fixtures and
the child are `test_addon`'s."""

# ruff: noqa: F811 -- `home`, `added` and `installed` are imported fixtures, taken by name.
import pytest

from cli.test_addon import ID, _install_team, _run, added, home, installed  # noqa: F401


def _disable_both(home, capsys):
    assert _run(capsys, "disable", "team@payments")[0] == 0
    assert _run(capsys, "disable", ID)[0] == 0


def _drop_the_parent(home, capsys):
    _disable_both(home, capsys)
    assert _run(capsys, "uninstall", ID)[0] == 0


@pytest.mark.parametrize(
    ("happens", "code", "says"),
    [
        pytest.param(None, 0, "team@payments: enabled", id="parent-loaded"),
        pytest.param(
            _disable_both,
            1,
            "cannot enable team@payments: its parent release@acme is not loaded: is disabled",
            id="parent-disabled",
        ),
        pytest.param(
            _drop_the_parent,
            1,
            "cannot enable team@payments: its parent release@acme is not installed; "
            "install it first: kraft admin addon install release@acme",
            id="parent-not-installed",
        ),
    ],
)
def test_enable_a_child_without_its_parent(installed, capsys, tmp_path, happens, code, says):
    """A child is enabled only while its parent loads and accepts it."""
    _install_team(installed, tmp_path)
    if happens:
        happens(installed, capsys)
    else:
        assert _run(capsys, "disable", "team@payments")[0] == 0
    before = installed.written()

    got, out = _run(capsys, "enable", "team@payments")

    assert got == code and says in out, out
    assert (installed.written() == before) is (code != 0)
