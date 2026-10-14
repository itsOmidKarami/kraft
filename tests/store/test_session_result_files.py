"""`store.result_files`: which files of the shared results folder are one
work item's."""

from support.store_fixtures import mk_item

from kraft import ids, item_paths, store


def _old(name: str) -> str:
    """A session id from before per-item ids, at their length."""
    return name.ljust(ids.LEGACY_LENGTH, "0")


async def test_result_files_are_the_items_own_and_only_those_that_exist(database, run_dirs):
    """Kraft-dni4n: what a sandboxed session may read of the results folder,
    which is one directory for every work item."""
    await mk_item(database)
    await mk_item(database, "w2")
    results = run_dirs.results

    def session(sid, wid="w1", result=None):
        return database.write(
            lambda c: store.create_session(
                c,
                id=sid,
                work_item_id=wid,
                node_id="verify",
                hook_point="on.test.run",
                log_path=f"/l/{sid}",
                result_path=str(result or results / f"{sid}.json"),
            )
        )

    s1, s2, s3, s5, other = (_old(n) for n in ("s1", "s2", "s3", "s5", "other"))
    await session(s1)
    await session(s2)  # never wrote its result file
    await session(s3, result=results / "escalation-w1-1.json")
    # A turn whose archive failed still names its thread's file: listed once,
    # since docker refuses the same mount target twice.
    await session(s5, result=results / "escalation-w1-1.json")
    await session(_old("s4"), result=results.parent / "elsewhere.json")
    await session(other, "w2")
    for name in (f"{s1}.json", f"{s1}.review.md", f"{s1}.instruction.md", "escalation-w1-1.json"):
        (results / name).write_text("mine")
    for name in (
        f"{other}.json",
        f"{other}.review.md",
        f"{other}.instruction.md",
        "stray.review.md",
    ):
        (results / name).write_text("theirs")
    (results.parent / "elsewhere.json").write_text("outside the folder")
    # What docker leaves where it was handed a source that did not exist.
    (results / f"{s2}.review.md").mkdir()
    (results / f"{s3}.review.md").symlink_to(results / f"{other}.review.md")

    found = database.read(lambda c: store.result_files(c, run_dirs, "w1"))

    assert found == [
        results / f"{s1}.json",
        results / f"{s1}.review.md",
        results / f"{s1}.instruction.md",
        results / "escalation-w1-1.json",
    ]


async def test_result_files_lists_older_files_and_session_folders(database, run_dirs):
    """An older session's files are under `results/`, named by its id; a
    newer one's are in its own folder. Both are its item's; nothing of another
    item's is listed, and a symlink never is. The session folder itself, with
    the log and exit code the host trusts, is never one of them, and nor is a
    file under `results/` named for a newer session: it never writes there, so
    another writer of that shared folder planted it."""
    s0 = _old("s0")
    s1 = item_paths.session_files(run_dirs, "w1", "s1abcd")
    other = item_paths.session_files(run_dirs, "w2", "s9abcd")
    for path in (
        run_dirs.results / f"{s0}.json",
        run_dirs.results / f"{s0}.review.md",
        run_dirs.results / "s1abcd.review.md",
        run_dirs.results / "s1abcd.instruction.md",
        s1.result,
        s1.instruction,
        s1.summary,
        s1.log,
        other.result,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    s1.review.symlink_to(s1.summary)
    await mk_item(database)
    await mk_item(database, "w2")
    for wid, sid, result in (
        ("w1", s0, run_dirs.results / f"{s0}.json"),
        ("w1", "s1abcd", s1.result),
        ("w2", "s9abcd", other.result),
    ):
        await database.write(
            lambda c, w=wid, s=sid, r=result: store.create_session(
                c,
                id=s,
                work_item_id=w,
                node_id="n",
                hook_point="n.t",
                log_path="/l",
                result_path=str(r),
            )
        )

    got = database.read(lambda c: store.result_files(c, run_dirs, "w1"))

    assert got == [
        run_dirs.results / f"{s0}.json",
        run_dirs.results / f"{s0}.review.md",
        s1.result,
        s1.instruction,
        s1.summary,
    ]
