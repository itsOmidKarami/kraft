"""`kl.py migrate --abandon`: a pre-V1 run `migrate` cannot move closed and
kept, so it blocks nothing; and a run that finished before V1, which needs no
move at all."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402
import kl_migrate  # noqa: E402

from .test_migrate import TEMPLATE, legacy_run, snapshot, stored  # noqa: E402


@pytest.mark.parametrize(
    "legacy",
    [
        pytest.param({"sliced": True}, id="sliced"),
        pytest.param({"template": "short"}, id="custom_chain"),
        pytest.param({"keep_epic": False}, id="no_epic"),
    ],
)
def test_abandon_closes_a_run_migrate_cannot_move(legacy, kl_cli, tmp_path):
    legacy_run(tmp_path, **legacy)
    legacy_run(tmp_path, chain_id="kl-bbbbbbbb")
    before = stored(tmp_path)
    frozen = snapshot(tmp_path / ".kraft-lite" / "chains")

    abandoned = kl_cli(tmp_path, "migrate", "--abandon", TEMPLATE)

    after = stored(tmp_path)
    # Kept, every one: closed and labelled, nothing deleted.
    assert after.keys() == before.keys()
    closed = sorted(
        id for id, r in before.items() if kl._of_chain(r, TEMPLATE) and r["status"] != "closed"
    )
    assert abandoned == {"abandoned": TEMPLATE, "closed": closed}
    for id, record in after.items():
        if id in closed:
            assert record["status"] == "closed"
            assert kl_migrate.ABANDONED_LABEL in record["labels"]
        else:
            assert record == before[id]
    assert snapshot(tmp_path / ".kraft-lite" / "chains") == frozen
    # The rest now moves.
    moved = kl_cli(tmp_path, "migrate")["chains"]
    assert [m["from"] for m in moved] == ["kl-bbbbbbbb"]


@pytest.mark.parametrize(
    "run, abandon",
    [
        pytest.param("v1", None, id="v1_run"),
        pytest.param("finished_legacy", TEMPLATE, id="finished_legacy_run"),
        pytest.param(None, "kl-cccccccc", id="no_such_run"),
        pytest.param(None, "../kl-aaaaaaaa", id="not_a_chain_id"),
    ],
)
def test_abandon_refuses_what_is_not_an_unfinished_legacy_run(run, abandon, kl_cli, tmp_path):
    if run == "v1":
        abandon = kl_cli(tmp_path, "start", "--title", "t")["chain_id"]
    elif run == "finished_legacy":
        legacy_run(tmp_path, at="post_merge_watch", state="closed")
    before = snapshot(tmp_path)

    refused = kl_cli(tmp_path, "migrate", "--abandon", abandon, ok=False)

    named = (
        f"{abandon!r} is not a chain id"
        if abandon.startswith(".")
        else f"{abandon} is not an unfinished run frozen before V1: "
        "--abandon closes only such a run"
    )
    assert f"kraft-lite: {named}" in refused.stderr and "Traceback" not in refused.stderr
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "how, verb",
    [
        *(
            pytest.param(how, verb, id=f"{how}-{verb}")
            for how in ("finished", "abandoned")
            for verb in ("state", "chains", "summary", "start", "migrate")
        ),
        pytest.param("finished", "close", id="finished-close"),
    ],
)
def test_a_finished_legacy_run_blocks_nothing(how, verb, kl_cli, tmp_path):
    """A run that finished, or was abandoned, before V1 has nothing to move: it
    answers `state` and `chains`, and only its summary needs the previous plugin."""
    if how == "finished":
        legacy_run(tmp_path, at="post_merge_watch", state="closed")
    else:
        legacy_run(tmp_path, sliced=True)
        kl_cli(tmp_path, "migrate", "--abandon", TEMPLATE)

    if verb == "state":
        state = kl_cli(tmp_path, "state")
        assert (state["chain_id"], state["status"], state["node"]) == (TEMPLATE, "done", None)
    elif verb == "chains":
        assert [(c["chain_id"], c["status"]) for c in kl_cli(tmp_path, "chains")] == [
            (TEMPLATE, "done")
        ]
    elif verb == "summary":
        refused = kl_cli(tmp_path, "summary", ok=False)
        assert (
            f"kraft-lite: chain {TEMPLATE} finished on the pre-V1 format; its summary needs "
            "the previous Kraft Lite" in refused.stderr
        )
    elif verb == "start":
        new_id = kl_cli(tmp_path, "start", "--title", "next")["chain_id"]
        assert kl_cli(tmp_path, "state")["chain_id"] == new_id
    elif verb == "migrate":
        assert kl_cli(tmp_path, "migrate") == {"bindings": {}, "chains": []}
    else:
        assert "no open node" in kl_cli(tmp_path, verb, ok=False).stderr
