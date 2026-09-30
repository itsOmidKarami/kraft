"""The model an agent session was launched with reaches its row (Kraft-9efnk.10):
codex and cursor report tokens under no model, and pricing needs one."""

from support.store_fixtures import mk_item

from kraft.adapters import subprocess as sp


def test_an_agent_launch_hands_its_model_to_the_session(run):
    assert run(model="opus")["model"] == "opus"


async def test_run_task_records_the_launch_model_on_the_row(database, run_dirs, tmp_path):
    await mk_item(database)
    await sp.run_task(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="n",
        hook_point="on.test.run",
        cmd=["true"],
        cwd=tmp_path,
        model="gpt-5.6-sol",
    )
    row = database.read(
        lambda c: c.execute("SELECT model FROM worker_sessions WHERE id = 's1'").fetchone()
    )
    assert row["model"] == "gpt-5.6-sol"
