"""tests/conftest.py's `client` fixture: the `edit_templates` marker option
composes across a module's `pytestmark` and a test's own mark, unlike every
other `api_client` option (a plain value, where the closest one just wins);
an indirect `request.param` is the closest of all."""

import os

import pytest


def _write_module_marker(tdir):
    (tdir / "module-marker.txt").write_text("module")


def _write_test_marker(tdir):
    (tdir / "test-marker.txt").write_text("test")


def _write_param_marker(tdir):
    # Written last, so it names what the marks' editors wrote before it.
    ran_before = sorted(p.name for p in tdir.glob("*-marker.txt"))
    (tdir / "param-marker.txt").write_text(" ".join(ran_before))


pytestmark = pytest.mark.api_client(edit_templates=_write_module_marker, default_setup=False)


@pytest.mark.api_client(edit_templates=_write_test_marker)
def test_a_tests_own_edit_templates_adds_to_its_modules(client, templates_dir):
    """Both editors ran: the test's own mark did not silently replace its
    module's `pytestmark` editor."""
    assert (templates_dir / "module-marker.txt").read_text() == "module"
    assert (templates_dir / "test-marker.txt").read_text() == "test"


@pytest.mark.api_client(host="0.0.0.0")
def test_a_test_that_is_not_parametrized_takes_its_marks(client):
    """No `request.param` at all: the marks alone decide, as before."""
    assert client.app.state.bound_host == "0.0.0.0"
    assert os.environ.get("KRAFT_BD_CWD")


@pytest.mark.api_client(host="0.0.0.0")
@pytest.mark.parametrize(
    ("client", "bound", "bd_cwd"),
    [({"host": "::1"}, "::1", True), ({"bd_workspace": False}, "0.0.0.0", False)],
    ids=["param-host-wins", "mark-host-kept"],
    indirect=["client"],
)
def test_an_indirect_param_is_merged_over_the_marks(client, bound, bd_cwd):
    """`indirect=["client"]` hands options per row, which a mark cannot: a
    key the row names beats the mark's, and a key it leaves out keeps it."""
    assert client.app.state.bound_host == bound
    assert bool(os.environ.get("KRAFT_BD_CWD")) is bd_cwd


@pytest.mark.api_client(edit_templates=_write_test_marker)
@pytest.mark.parametrize(
    "client", [{"edit_templates": _write_param_marker}], ids=["param-editor"], indirect=True
)
def test_an_indirect_edit_templates_runs_after_the_marks(client, templates_dir):
    """`edit_templates` composes here too: the module's editor, the test's,
    then the row's, each on top of the one before."""
    assert (templates_dir / "module-marker.txt").read_text() == "module"
    assert (templates_dir / "test-marker.txt").read_text() == "test"
    assert (templates_dir / "param-marker.txt").read_text() == "module-marker.txt test-marker.txt"
