"""`item create --chain-file` and `item set-chain --chain-file`: a one-off chain from a file."""

from __future__ import annotations

import pytest

from kraft import cli

TEXT = "extends: default\nremove: [work_brief]\n"


@pytest.fixture
def chain_file(tmp_path):
    path = tmp_path / "chain.yaml"
    path.write_text(TEXT)
    return str(path)


def _fake(monkeypatch, name, answer):
    seen = {}

    async def fake(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return answer

    monkeypatch.setattr(cli.item.client, name, fake)
    return seen


def test_create_sends_the_file_as_the_one_off_chain(monkeypatch, capsys, chain_file):
    seen = _fake(monkeypatch, "create_work_item", {"id": "w1", "status": "paused", "title": "t"})
    cli.main(["item", "create", "t", "--repo", "/r", "--chain-file", chain_file])
    assert seen["kwargs"]["one_off_chain"] == TEXT
    assert seen["args"][2] is None  # no library chain named


@pytest.mark.parametrize(
    ("argv", "words"),
    [
        pytest.param(
            ["item", "create", "t", "--chain", "q", "--chain-file", "{f}"],
            "not allowed with",
            id="create-both",
        ),
        pytest.param(
            ["item", "set-chain", "w1", "--chain", "q", "--chain-file", "{f}"],
            "not allowed with",
            id="set-both",
        ),
        pytest.param(["item", "set-chain", "w1"], "required", id="set-neither"),
        pytest.param(
            ["item", "create", "t", "--chain-file", "/no/such/file.yaml"],
            "chain file",
            id="create-missing-file",
        ),
    ],
)
def test_a_chain_and_a_chain_file_are_exclusive(capsys, chain_file, argv, words):
    with pytest.raises(SystemExit) as stop:
        cli.main([a.replace("{f}", chain_file) for a in argv])
    assert stop.value.code == 2
    assert words in capsys.readouterr().err


def test_set_chain_sends_the_file(monkeypatch, capsys, chain_file):
    seen = _fake(monkeypatch, "set_chain", {"id": "w1", "chain_template": "@one-off"})
    cli.main(["item", "set-chain", "w1", "--chain-file", chain_file])
    assert seen["args"] == (None, "w1")
    assert seen["kwargs"] == {"one_off_chain": TEXT}


@pytest.mark.parametrize(
    ("custom", "said"),
    [(2, True), (0, False)],
    ids=["custom-says-a-person-reviews-it", "library-only-says-nothing"],
)
def test_create_prints_the_review_line_for_custom_content(
    monkeypatch, capsys, chain_file, custom, said
):
    _fake(
        monkeypatch,
        "create_work_item",
        {"id": "w1", "status": "paused", "title": "t", "one_off": {"custom": custom}},
    )
    cli.main(["item", "create", "t", "--repo", "/r", "--chain-file", chain_file])
    out = capsys.readouterr().out
    assert ("which a person reviews in the web UI before it can start" in out) is said
