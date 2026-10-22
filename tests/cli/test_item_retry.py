"""`kraft item retry --sandbox-from-config` (Kraft-ajzx7): split from
test_item.py, which is at its line budget."""

from __future__ import annotations

import pytest

from kraft import cli


@pytest.mark.parametrize(
    ("yes", "retried"), [(False, []), (True, [True])], ids=["shows-only", "yes-applies"]
)
def test_sandbox_from_config_shows_both_and_needs_yes(monkeypatch, capsys, yes, retried):
    """Without --yes it reads both sandboxes and changes nothing (Kraft-ajzx7)."""
    change = {"changed": True, "old": {"image": "a"}, "new": {"image": "b"}}
    calls = []

    async def preview(*_a, **_kw):
        return {"id": "w1", **change}

    async def retry(*_a, sandbox_from_config=False, **_kw):
        calls.append(sandbox_from_config)
        return {"id": "w1", "node_id": "verify", "attempt": 2, "sandbox": change}

    monkeypatch.setattr(cli.item.client, "sandbox_from_config", preview)
    monkeypatch.setattr(cli.item.client, "retry", retry)
    argv = ["item", "retry", "w1", "--sandbox-from-config", *(["--yes"] if yes else [])]
    if yes:
        cli.main(argv)
    else:
        with pytest.raises(SystemExit):
            cli.main(argv)
    out = capsys.readouterr()
    assert calls == retried
    assert "{'image': 'a'}\n      -> {'image': 'b'}" in out.out
    assert ("pass --yes" in out.err) is not yes
    assert ("retried w1 at verify, attempt 2" in out.out) is yes
