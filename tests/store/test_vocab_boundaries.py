import pytest
from support import schema

from kraft import store
from kraft.vocab import TRAITS, WorkItemStatus


def test_claim_for_run_refuses_a_stray_status(tmp_path):
    conn = schema.fresh(tmp_path)
    schema.insert_item(conn)
    with pytest.raises(ValueError, match="WorkItemStatus"):
        store.claim_for_run(conn, "w1", from_statuses=["bogus"])
    with pytest.raises(ValueError, match="WorkItemStatus"):
        store.claim_for_run(conn, "w1", from_statuses=["paused"], to_status="bogus")


def test_a_status_member_binds_and_reads_back_as_its_plain_value(
    tmp_path,
):  # sqlite round-trip the sweep relies on
    conn = schema.fresh(tmp_path)
    schema.insert_item(conn, status=WorkItemStatus.PAUSED)
    row = conn.execute("SELECT status FROM work_items WHERE id = 'w1'").fetchone()
    assert type(row["status"]) is str and row["status"] == "paused"


@pytest.mark.parametrize(
    ("mark", "status"),
    [
        (store.mark_waiting, WorkItemStatus.WAITING),
        (store.mark_rate_limited, WorkItemStatus.RATE_LIMITED),
    ],
    ids=["waiting", "rate_limited"],
)
def test_the_stop_kind_a_writer_stores_is_the_one_the_status_implies(tmp_path, mark, status):
    conn = schema.fresh(tmp_path)
    schema.insert_item(conn)
    mark(conn, "w1", "n", "2026-01-01T00:00:00+00:00")
    stored = conn.execute("SELECT stop_kind FROM work_items WHERE id = 'w1'").fetchone()[0]
    assert stored == TRAITS[status].implied_stop_kind
