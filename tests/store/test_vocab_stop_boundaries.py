import pytest
from support import schema

from kraft import store


@pytest.mark.parametrize("kind", ["wait", "rate_limit", "gate", "bogus"])
def test_mark_needs_human_refuses_a_kind_that_is_not_a_needs_human_stop(tmp_path, kind):
    conn = schema.fresh(tmp_path)
    schema.insert_item(conn)
    with pytest.raises(ValueError):
        store.mark_needs_human(conn, "w1", "n", "why", kind=kind)
