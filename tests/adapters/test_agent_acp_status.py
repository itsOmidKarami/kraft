import pytest

from kraft.acp import log
from kraft.adapters import agent
from kraft.paths import RunDirs


@pytest.mark.parametrize(
    ("events", "status", "reader", "expected"),
    [
        (["config_error"], "failed", "acp", "config_error"),
        (["error"], "failed", "acp", "failed"),
        ([], "done", "acp", "done"),
        (["config_error"], "failed", "claude-stream-json", "failed"),
        (["policy_violation"], "done", "acp", "config_error"),
        (["policy_violation"], "done", "claude-stream-json", "done"),
        (["rejection_limit"], "failed", "acp", "config_error"),
    ],
    ids=[
        "config-error-event",
        "plain-error",
        "clean",
        "other-reader-untouched",
        "policy-violation-beats-done",
        "policy-violation-only-for-acp",
        "rejection-limit",
    ],
)
def test_a_logged_config_error_is_the_sessions_status(tmp_path, events, status, reader, expected):
    path = tmp_path / "log"
    path.write_text("".join(log.event(e, message="m") + "\n" for e in events))
    resolve = agent._resolve_status(None, "w1", RunDirs(base=tmp_path), tmp_path, reader)
    assert resolve(status, path, 1) == expected
