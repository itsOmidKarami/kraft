"""`support.os_gates`' process listing survives a process whose argv cannot be read."""

from __future__ import annotations

import psutil
import pytest
from support import os_gates


class _Proc:
    def __init__(self, argv=None, error=None):
        self.argv, self.error, self.killed = argv, error, False

    def cmdline(self):
        if self.error:
            raise self.error
        return self.argv

    def kill(self):
        self.killed = True


@pytest.fixture
def listing(monkeypatch):
    good = _Proc(["sleep", "0.2"])
    rows = [
        _Proc(error=SystemError("proc_cmdline returned a result with an exception set")),
        _Proc(error=psutil.NoSuchProcess(1)),
        _Proc(error=psutil.AccessDenied(1)),
        good,
    ]
    # the real `process_iter(["cmdline"])` would raise out of the listing at the first bad row
    monkeypatch.setattr(os_gates.psutil, "process_iter", lambda *a, **kw: iter(rows))
    return good


def test_procs_skips_unreadable_processes(listing):
    assert os_gates.procs("sleep", "0.2") == [listing]


def test_kill_where_kills_only_matches(listing):
    os_gates.kill_where(lambda c: "sleep" in c)
    assert listing.killed
