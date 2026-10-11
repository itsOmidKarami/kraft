"""`kraft.adapters.subprocess._kill_group`'s signalling, apart from the real
child processes `test_subprocess.py` launches."""

import signal

from kraft.adapters import subprocess as sp


async def test_kill_group_signals_again_for_a_member_forked_as_the_first_sigterm_landed(
    monkeypatch,
):
    """Seen as 2 in 300 and 5 in 400 kills: `sh` dies on SIGTERM while the `sleep` it was
    forking misses it, so one SIGTERM left the group alive for the whole grace.
    The second SIGTERM is what ends it; here the group only dies on that one."""
    sent = []

    def killpg(pgid, sig):
        sent.append(sig)
        if sent.count(signal.SIGTERM) >= 2:
            raise ProcessLookupError

    monkeypatch.setattr(sp.os, "killpg", killpg)
    await sp._kill_group(1, grace=2.0)

    assert sent == [signal.SIGTERM, signal.SIGTERM]
