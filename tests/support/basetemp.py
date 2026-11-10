"""pytest's keep-last-3 cleanup of old basetemps keeps working (Kraft-t7aeb)."""

import contextlib
import os
import stat

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session):
    """Before that cleanup runs, so it acts on both.

    The add-on store's folders are read-only (`addons/fetch.write_store`), and
    `rm -rf` or a plain `shutil.rmtree` cannot empty a read-only folder, so
    this run's are made writable again. And a run killed before its end keeps
    its basetemp's `.lock`, which pytest honours for three days: one whose
    process is gone is dropped, so this run's cleanup removes that basetemp.
    Best effort: nothing here may fail a session.
    """
    basetemp = session.config._tmp_path_factory._basetemp
    if basetemp is None or hasattr(session.config, "workerinput"):
        return  # none made; or an xdist worker, whose folders the controller does
    for dirpath, _dirs, _files in os.walk(basetemp):
        mode = os.lstat(dirpath).st_mode
        if not mode & stat.S_IWUSR:
            with contextlib.suppress(OSError):
                os.chmod(dirpath, mode | stat.S_IWUSR)
    if not basetemp.parent.name.startswith("pytest-of-"):
        return  # a --basetemp of the caller's: its siblings are not pytest's
    for lock in [*basetemp.parent.glob("pytest-*/.lock"), *basetemp.parent.glob("garbage-*/.lock")]:
        try:
            os.kill(int(lock.read_text()), 0)
        except ProcessLookupError:
            with contextlib.suppress(OSError):
                lock.unlink()
        except (OSError, ValueError, OverflowError):
            pass  # alive, someone else's, or not a pid: pytest decides
