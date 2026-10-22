"""The results folder against a real container runtime: the one proof that
the per-file mounts `docker_argv` builds show a session its own work item's
files and nothing of another's (Kraft-dni4n), and a session its own `out/` to write."""

import json
import shlex
import subprocess

from support.store_fixtures import mk_item

from kraft import ids, store
from kraft.worker.backends import docker

#: Sessions from before per-item ids, the only ones with files in `results/`.
_SID = {n: n.ljust(ids.LEGACY_LENGTH, "0") for n in ("earlier", "mine", "theirs")}


async def test_a_sandboxed_session_sees_only_its_own_items_results(
    database, run_dirs, tmp_path, git_image
):
    results = run_dirs.results
    for wid, sessions in (("w1", ("earlier", "mine")), ("w2", ("theirs",))):
        await mk_item(database, wid)
        for sid in (_SID[n] for n in sessions):
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
    file = {
        n: results / f"{_SID[n.split('.')[0]]}.{n.split('.', 1)[1]}"
        for n in ("earlier.json", "mine.review.md", "theirs.json", "theirs.review.md")
    }
    for name, path in file.items():
        path.write_text(f"{name}\n")
    own = results / f"{_SID['mine']}.json"
    own.touch()  # as `run_task` does: docker binds only a file that exists
    worktree = tmp_path / "wt"
    worktree.mkdir()
    q = {n: shlex.quote(str(file[n])) for n in ("earlier.json", "mine.review.md")}
    script = (
        f"set -e; cat {q['mine.review.md']} {q['earlier.json']};"
        f" ls {shlex.quote(str(results))};"
        f" if cat {shlex.quote(str(file['theirs.review.md']))}; then exit 3; fi;"
        # Its own item's earlier result is to read, never to rewrite.
        f" if echo forged 2>/dev/null > {q['earlier.json']}; then exit 4; fi;"
        f' echo \'{{"status": "done"}}\' > {shlex.quote(str(own))}'
    )

    argv = docker.docker_argv(
        ["sh", "-c", script],
        worktree,
        {"kind": "docker", "image": git_image},
        database.read(lambda c: store.result_files(c, run_dirs, "w1")),
        result_path=own,
    )
    ran = subprocess.run(argv, capture_output=True, text=True)

    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.splitlines() == [
        "mine.review.md",
        "earlier.json",
        file["earlier.json"].name,
        own.name,
        file["mine.review.md"].name,
    ]
    assert json.loads(own.read_text()) == {"status": "done"}
    assert file["earlier.json"].read_text() == "earlier.json\n"


def test_a_sandboxed_session_writes_its_result_by_rename(tmp_path, git_image):
    """A temporary file beside the result, then `mv` onto it, as a harness
    writing atomically does: the session's `out/` is its folder to write, so
    the rename lands where Kraft reads (Kraft-l9ive)."""
    out = tmp_path / "items" / "w1" / "sessions" / "s1" / "out"
    out.mkdir(parents=True)
    result = out / "result.json"
    result.touch()  # as `run_task` does
    worktree = tmp_path / "wt"
    worktree.mkdir()
    q = shlex.quote(str(result))
    done = shlex.quote(json.dumps({"status": "done"}))
    script = f'set -e; t=$(mktemp {q}.XXXXXX); echo {done} > "$t"; mv "$t" {q}'

    argv = docker.docker_argv(
        ["sh", "-c", script],
        worktree,
        {"kind": "docker", "image": git_image},
        [],
        result_path=result,
        rw_files=(result,),
        rw_dirs=(out,),
    )
    ran = subprocess.run(argv, capture_output=True, text=True)

    assert ran.returncode == 0, ran.stderr
    assert json.loads(result.read_text()) == {"status": "done"}
