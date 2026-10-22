"""What a sandboxed session may write outside its worktree: its own `out/`
folder, where its two session files are, and its item's documents. Everything
else of its item's sessions is read-only, file by file, and no session folder
is ever mounted. In its own file because `test_docker.py` is at its line
budget (`dev/check_tests.py`)."""

from __future__ import annotations

from kraft.worker.backends import docker
from worker.backends.test_docker import _volumes

S0 = "/run/items/w/sessions/s0"
S1 = "/run/items/w/sessions/s1"


def test_a_session_writes_only_its_own_out_folder_and_its_item_s_docs():
    argv = docker.docker_argv(
        ["claude"],
        "/work/item-1",
        {"kind": "docker", "image": "x"},
        [
            f"{S0}/out/result.json",
            f"{S0}/out/summary.md",
            f"{S1}/instruction.md",
            f"{S1}/out/result.json",
        ],
        result_path=f"{S1}/out/result.json",
        rw_files=(f"{S1}/out/result.json", f"{S1}/out/summary.md"),
        rw_dirs=("/run/items/w/docs", f"{S1}/out"),
    )

    run = [v for v in _volumes(argv) if v.startswith("/run/")]
    assert [v for v in run if v.endswith(":ro")] == [
        f"{S0}/out/result.json:{S0}/out/result.json:ro",
        f"{S0}/out/summary.md:{S0}/out/summary.md:ro",
        f"{S1}/instruction.md:{S1}/instruction.md:ro",
    ]
    # The folder, not each file in it: a file mount is a mount point, and a
    # result written by rename onto it fails (Kraft-l9ive).
    assert [v for v in run if not v.endswith(":ro")] == [
        "/run/items/w/docs:/run/items/w/docs",
        f"{S1}/out:{S1}/out",
    ]
    mounted = {v.split(":")[0] for v in run}
    assert not mounted & {S0, S1, f"{S0}/out"}, "no session folder is mounted"
