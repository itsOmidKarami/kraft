"""`kraft admin stop`'s wait on a server that is undoing a cut-short rebase."""

from __future__ import annotations

import subprocess
import sys
import threading

import pytest

from kraft import cli
from kraft.paths import RunDirs


@pytest.mark.parametrize("rebase", [True, False], ids=["undoing-a-rebase", "nothing-to-undo"])
def test_stop_waits_out_a_rebase_abort_past_the_usual_wait(tmp_path, monkeypatch, capsys, rebase):
    """A server stopped mid-rebase undoes it on its way out, which outlasts the
    usual wait: `stop` says it is still stopping because of that and waits,
    where a server that is merely slow is still reported as not stopped."""
    run = tmp_path / "run"
    monkeypatch.setenv("KRAFT_RUN_DIR", str(run))
    monkeypatch.setattr(cli.admin, "_STOP_WAIT_S", 0.3)
    monkeypatch.setattr("kraft.builtins.REBASE_UNDO_MAX_S", 5.0)
    gitdir = tmp_path / "gitdir"
    (gitdir / ("rebase-merge" if rebase else "info")).mkdir(parents=True)
    (run / "worktrees" / "w1").mkdir(parents=True)
    (run / "worktrees" / "w1" / ".git").write_text(f"gitdir: {gitdir}\n")
    pid_path = RunDirs(run).pid
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,sys,time\nfrom kraft import pidfile\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGTERM, lambda *a: (time.sleep(1.5), sys.exit(0)))\n"
            "fd = pidfile.hold(Path(sys.argv[1])); print(flush=True); time.sleep(30)\n",
            str(pid_path),
        ],
        stdout=subprocess.PIPE,
    )
    reaper = threading.Thread(target=proc.wait, daemon=True)
    reaper.start()
    try:
        proc.stdout.readline()
        if rebase:
            cli.main(["admin", "stop"])
        else:
            with pytest.raises(SystemExit) as raised:
                cli.main(["admin", "stop"])
            assert raised.value.code == 1
        out, err = capsys.readouterr()
        if rebase:
            assert f"stopped (pid {proc.pid})" in out
            assert "still stopping: a git rebase is being undone" in err
        else:
            assert "did not stop within 0.3s" in err
    finally:
        if proc.poll() is None:
            proc.kill()
        reaper.join(timeout=5)
