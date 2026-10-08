import pytest
from support import schema

from kraft import store


def test_session_exited_refuses_a_stray_status(tmp_path):
    conn = schema.fresh(tmp_path)
    schema.insert_item(conn)
    schema.insert_session(conn)
    with pytest.raises(ValueError, match="SessionStatus"):
        store.session_exited(conn, "s1", "ci_pending")
