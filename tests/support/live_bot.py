"""A bot whose threads are `fake.threads`: what Kraft posts is what the next read shows."""

from dataclasses import replace

from kraft.adapters import forge


def live_bot(fake, bot, active, monkeypatch):
    """`fake.reviewer_feedback` answers with the threads `fake.threads` holds now, so Kraft's
    replies appear in the next read and a test adds the bot's answer by editing the thread."""

    async def read(*, repo, branch, reviewers, since, kraft=None):
        threads = tuple(
            {"id": t.id, "file": t.file, "line": t.line, "body": t.notes[0].body, "notes": t.notes}
            for t in fake.threads.values()
            if not t.resolved
        )
        return [forge.ReviewerFeedback(bot, active, threads=threads)]

    monkeypatch.setattr(fake, "reviewer_feedback", read)


def bot_says(fake, tid, note):
    """The bot adds `note` (a `support.bot_threads.note`) to thread `tid`."""
    t = fake.threads[tid]
    fake.threads[tid] = replace(t, notes=(*t.notes, note))


def counting(fake, monkeypatch, name):
    """Wrap `fake.<name>` so a test can count its calls; returns the list of them."""
    calls: list = []
    inner = getattr(fake, name)

    async def wrapped(*args, **kw):
        calls.append((args, kw))
        return await inner(*args, **kw)

    monkeypatch.setattr(fake, name, wrapped)
    return calls
