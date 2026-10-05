"""`store.result_files`: which files of the shared results folder are one
work item's."""

from support.store_fixtures import mk_item

from kraft import store


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

    await session("s1")
    await session("s2")  # never wrote its result file
    await session("s3", result=results / "escalation-w1-1.json")
    # A turn whose archive failed still names its thread's file: listed once,
    # since docker refuses the same mount target twice.
    await session("s5", result=results / "escalation-w1-1.json")
    await session("s4", result=results.parent / "elsewhere.json")
    await session("other", "w2")
    for name in ("s1.json", "s1.review.md", "s1.instruction.md", "escalation-w1-1.json"):
        (results / name).write_text("mine")
    for name in ("other.json", "other.review.md", "other.instruction.md", "stray.review.md"):
        (results / name).write_text("theirs")
    (results.parent / "elsewhere.json").write_text("outside the folder")
    # What docker leaves where it was handed a source that did not exist.
    (results / "s2.review.md").mkdir()
    (results / "s3.review.md").symlink_to(results / "other.review.md")

    found = database.read(lambda c: store.result_files(c, results, "w1"))

    assert found == [
        results / "s1.json",
        results / "s1.review.md",
        results / "s1.instruction.md",
        results / "escalation-w1-1.json",
    ]
