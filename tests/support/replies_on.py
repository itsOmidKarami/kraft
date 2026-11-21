"""Fixtures for Kraft's replies to the review bots, loaded for every test
through `tests/conftest.py`'s `pytest_plugins`."""

import pytest


@pytest.fixture
def replies_on(monkeypatch):
    """Lift the load refusal of `replies: true` (`automated_review.REPLIES_ARRIVED`),
    as Part 2's third item will. Every test that turns replies on takes it:

        def test_x(replies_on):
            BotReviewer(bot="a", replies=True)
    """
    from kraft import automated_review

    monkeypatch.setattr(automated_review, "REPLIES_ARRIVED", True)


@pytest.fixture(autouse=True)
def _fresh_reply_spacing(monkeypatch):
    """`send` keeps when each forge account last wrote, for the life of the process."""
    from kraft.adapters.forge.replies import send

    async def instant(_seconds):
        return None

    monkeypatch.setattr(send, "_last_write", {})
    monkeypatch.setattr(send, "_locks", {})
    # Writes are spaced a second apart per account; never a real second in a test.
    # `tests/adapters/forge/replies/test_send.py` patches `_sleep` with its own recorder.
    monkeypatch.setattr(send, "_sleep", instant)
