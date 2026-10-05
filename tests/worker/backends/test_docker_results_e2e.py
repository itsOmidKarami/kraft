"""The results folder against a real container runtime: the one proof that
the per-file mounts `docker_argv` builds show a session its own work item's
files and nothing of another's (Kraft-dni4n)."""

import json
import shlex
import subprocess

from support.store_fixtures import mk_item

from kraft import store
from kraft.worker.backends import docker


async def test_a_sandboxed_session_sees_only_its_own_items_results(
    database, run_dirs, tmp_path, git_image
):
    results = run_dirs.results
    for wid, sessions in (("w1", ("earlier", "mine")), ("w2", ("theirs",))):
        await mk_item(database, wid)
        for sid in sessions:
            await database.write(
                lambda c, wid=wid, sid=sid: store.create_session(
                    c,
                    id=sid,
                    work_item_id=wid,
                    node_id="verify",
                    hook_point="on.review.run",
                    log_path=f"/l/{sid}",
                    result_path=str(results / f"{sid}.json"),
                )
            )
    for name in ("earlier.json", "mine.review.md", "theirs.json", "theirs.review.md"):
        (results / name).write_text(f"{name}\n")
    own = results / "mine.json"
    own.touch()  # as `run_task` does: docker binds only a file that exists
    worktree = tmp_path / "wt"
    worktree.mkdir()
    q = {n: shlex.quote(str(results / n)) for n in ("earlier.json", "mine.review.md")}
    script = (
        f"set -e; cat {q['mine.review.md']} {q['earlier.json']};"
        f" ls {shlex.quote(str(results))};"
        f" if cat {shlex.quote(str(results / 'theirs.review.md'))}; then exit 3; fi;"
        # Its own item's earlier result is to read, never to rewrite.
        f" if echo forged 2>/dev/null > {q['earlier.json']}; then exit 4; fi;"
        f' echo \'{{"status": "done"}}\' > {shlex.quote(str(own))}'
    )

    argv = docker.docker_argv(
        ["sh", "-c", script],
        worktree,
        {"kind": "docker", "image": git_image},
        database.read(lambda c: store.result_files(c, results, "w1")),
        result_path=own,
    )
    ran = subprocess.run(argv, capture_output=True, text=True)

    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.splitlines() == [
        "mine.review.md",
        "earlier.json",
        "earlier.json",
        "mine.json",
        "mine.review.md",
    ]
    assert json.loads(own.read_text()) == {"status": "done"}
    assert (results / "earlier.json").read_text() == "earlier.json\n"
