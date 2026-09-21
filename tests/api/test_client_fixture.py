"""tests/conftest.py's `client` fixture: the `edit_templates` marker option
composes across a module's `pytestmark` and a test's own mark, unlike every
other `api_client` option (a plain value, where the closest one just wins)."""

import pytest


def _write_module_marker(tdir):
    (tdir / "module-marker.txt").write_text("module")


def _write_test_marker(tdir):
    (tdir / "test-marker.txt").write_text("test")


pytestmark = pytest.mark.api_client(edit_templates=_write_module_marker, default_setup=False)


@pytest.mark.api_client(edit_templates=_write_test_marker)
def test_a_tests_own_edit_templates_adds_to_its_modules(client, templates_dir):
    """Both editors ran: the test's own mark did not silently replace its
    module's `pytestmark` editor."""
    assert (templates_dir / "module-marker.txt").read_text() == "module"
    assert (templates_dir / "test-marker.txt").read_text() == "test"
