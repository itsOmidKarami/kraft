"""`kraft admin addon enable` for an add-on that builds on another: the
child is enabled only while its parent loads and accepts it. The fixtures and
the child are `test_addon`'s."""

# ruff: noqa: F811 -- `home`, `added` and `installed` are imported fixtures, taken by name.
import pytest

from cli.test_addon import ID, _install_team, _run, added, home, installed  # noqa: F401


def _child_disabled(home, tmp_path, capsys, accepts="1"):
    _install_team(home, tmp_path, accepts)
    assert _run(capsys, "disable", "team@payments")[0] == 0


def _disable_both(home, tmp_path, capsys):
    _child_disabled(home, tmp_path, capsys)
    assert _run(capsys, "disable", ID)[0] == 0


def _drop_the_parent(home, tmp_path, capsys):
    _disable_both(home, tmp_path, capsys)
    assert _run(capsys, "uninstall", ID)[0] == 0


def _parent_out_of_range(home, tmp_path, capsys):
    """The child takes `release@acme` 2.x; 1.0.0 is installed."""
    _child_disabled(home, tmp_path, capsys, accepts="2")


@pytest.mark.parametrize(
    ("setup", "code", "says"),
    [
        pytest.param(_child_disabled, 0, "team@payments: enabled", id="parent-loaded"),
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
        pytest.param(
            _parent_out_of_range,
            1,
            "cannot enable team@payments: its parent release@acme is 1.0.0, outside its range 2",
            id="parent-out-of-range",
        ),
    ],
)
def test_enable_a_child_without_its_parent(installed, capsys, tmp_path, setup, code, says):
    """A child is enabled only while its parent loads and accepts it."""
    setup(installed, tmp_path, capsys)
    before = installed.written()

    got, out = _run(capsys, "enable", "team@payments")

    assert got == code and says in out, out
    assert ("install it first" in out) is ("install it first" in says), out
    assert (installed.written() == before) is (code != 0)
