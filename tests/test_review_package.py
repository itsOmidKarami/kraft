"""Sub-project F §9: one producer, two consumers.

The human approving a gate and the reviewer that gated it have to be looking at
the same change, so `review.read_change` backs both `GET /work-items/{wid}/diff`
and the package a review agent is handed by path.
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from support.harness import fake_registry, isolated_bd, make_repo

from kraft import config, db, executor, review
from kraft.paths import RunDirs
from kraft.templates import Registry, Template

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


def _head(repo: Path) -> str:
    return config.git_read(repo, "rev-parse", "HEAD")


def test_read_change_sees_uncommitted_work_and_untracked_files(tmp_path):
    """The range is the working tree against base, not base..HEAD: an agent that
    wrote files without committing them is the normal mid-chain state."""
    repo = make_repo(tmp_path)
    base = _head(repo)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "brand_new.py").write_text("x = 1\n")

    change = review.read_change(repo, base)
    assert change is not None
    assert [f["path"] for f in change.files] == ["calc.py"]
    assert change.untracked == ["brand_new.py"]
    assert "a + b" in change.diff
    assert change.commits == []  # nothing committed past base


def test_read_change_lists_commits_past_the_base(tmp_path):
    repo = make_repo(tmp_path)
    base = _head(repo)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    config.git_read(repo, "add", "-A")
    import subprocess

    subprocess.run(["git", "commit", "-m", "fix add"], cwd=repo, capture_output=True, check=True)

    change = review.read_change(repo, base)
    assert change is not None
    assert len(change.commits) == 1
    assert "fix add" in change.commits[0]


def test_read_change_is_none_when_git_fails(tmp_path):
    """None, not an empty diff: to the human about to approve, "git broke" and
    "nothing changed" must not look the same."""
    repo = make_repo(tmp_path)
    assert review.read_change(repo, "not-a-ref") is None


def test_the_package_carries_more_context_than_the_gate_viewer(tmp_path):
    """-U10 is the point of the package: ten lines of context per hunk turns one
    Read into the whole review surface."""
    repo = make_repo(tmp_path)
    (repo / "wide.py").write_text("\n".join(f"line{i} = {i}" for i in range(40)) + "\n")
    config.git_read(repo, "add", "-A")
    import subprocess

    subprocess.run(["git", "commit", "-m", "wide"], cwd=repo, capture_output=True, check=True)
    base = _head(repo)
    lines = (repo / "wide.py").read_text().splitlines()
    lines[20] = "line20 = 999"
    (repo / "wide.py").write_text("\n".join(lines) + "\n")

    narrow = review.read_change(repo, base)
    wide = review.read_change(repo, base, context=review.PACKAGE_CONTEXT)
    assert narrow is not None and wide is not None
    assert len(wide.diff.splitlines()) > len(narrow.diff.splitlines())


def test_write_package_names_the_file_by_the_session(tmp_path):
    repo = make_repo(tmp_path)
    base = _head(repo)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    results = tmp_path / "results"
    results.mkdir()

    path = review.write_package(results, repo, base, "sess1")
    assert path is not None
    assert path.name == "sess1.review.md"
    body = path.read_text()
    assert "## Commits" in body and "## Files changed" in body and "## Diff" in body
    assert "a + b" in body


def test_two_items_on_the_same_base_do_not_share_a_package(tmp_path):
    """`run_dirs.results` is one directory for every work item, and uncommitted
    work is the normal mid-chain state — so naming by the base..HEAD range gave
    two items branched from the same commit the same file, and one reviewer the
    other item's diff."""
    results = tmp_path / "results"
    results.mkdir()
    repo_a = make_repo(tmp_path, name="a")
    repo_b = make_repo(tmp_path, name="b")
    # same base sha, neither has committed anything past it
    assert _head(repo_a) == _head(repo_b)
    (repo_a / "calc.py").write_text("A CHANGED THIS\n")
    (repo_b / "calc.py").write_text("B CHANGED THIS\n")

    a = review.write_package(results, repo_a, _head(repo_a), "sess-a")
    b = review.write_package(results, repo_b, _head(repo_b), "sess-b")
    assert a is not None and b is not None
    assert a != b
    assert "A CHANGED THIS" in a.read_text()
    assert "B CHANGED THIS" in b.read_text()


def test_write_package_returns_none_when_git_fails(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    assert review.write_package(results, make_repo(tmp_path), "not-a-ref", "sess1") is None
    assert list(results.iterdir()) == []


def _review_template() -> Template:
    return Template(
        id="review-only",
        nodes=[
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None, "fix_loop": None},
            {
                "id": "review",
                "tasks": ["on.review.local.run"],
                "gate_after": None,
                "fix_loop": None,
            },
        ],
    )


def test_a_review_agent_is_handed_the_package_by_path(tmp_path, monkeypatch):
    """The path reaches the agent as $KRAFT_REVIEW_PACKAGE and the diff never
    passes through the prompt."""
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    base = fake_registry(sys.executable, _FAKE_AGENT)
    registry = Registry(
        hooks={**base.hooks, "on.review.local.run": base.hooks["on.implementation.start"]}
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="review me",
                repo=str(repo),
                template=_review_template(),
                bd_cwd=str(tracker),
            )
            await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            return rd
        finally:
            await database.close()

    rd = asyncio.run(scenario())

    packages = sorted(rd.results.glob("*.review.md"))
    assert len(packages) == 1, f"expected one review package, got {packages}"

    # by path, never by content: the diff must not be inside the prompt
    argv = [json.loads(ln) for ln in argv_log.read_text().splitlines()]
    joined = " ".join(a for line in argv for a in line)
    assert "$KRAFT_REVIEW_PACKAGE" in joined
    assert packages[0].read_text() not in joined


def test_a_non_review_hook_gets_no_package(tmp_path, monkeypatch):
    """Everything else is working *in* the diff, not judging it."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    registry = fake_registry(sys.executable, _FAKE_AGENT)

    template = Template(
        id="impl-only",
        nodes=[
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None, "fix_loop": None},
            {
                "id": "implementation",
                "tasks": ["on.implementation.start"],
                "gate_after": None,
                "fix_loop": None,
            },
        ],
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="build me",
                repo=str(repo),
                template=template,
                bd_cwd=str(tracker),
            )
            await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            return rd
        finally:
            await database.close()

    rd = asyncio.run(scenario())
    assert list(rd.results.glob("*.review.md")) == []


def test_read_change_with_head_excludes_uncommitted_work(tmp_path):
    """Kraft-nceo. `base..HEAD` is what earlier nodes committed; the working
    tree is what the current node is doing. One range showed them as one
    change."""
    repo = make_repo(tmp_path)
    base = _head(repo)
    (repo / "doc.md").write_text("landed paperwork\n")
    config.git_read(repo, "add", "-A")
    subprocess.run(
        ["git", "commit", "-m", "land the doc"], cwd=repo, capture_output=True, check=True
    )
    (repo / "calc.py").write_text("in flight code\n")
    (repo / "brand_new.py").write_text("x = 1\n")

    landed = review.read_change(repo, base, head="HEAD")
    assert landed is not None
    assert [f["path"] for f in landed.files] == ["doc.md"]
    assert "landed paperwork" in landed.diff
    assert "in flight code" not in landed.diff
    # `git status --porcelain` is only meaningful for the working tree
    assert landed.untracked == []
    assert len(landed.commits) == 1 and "land the doc" in landed.commits[0]


def test_write_package_still_spans_base_to_working_tree(tmp_path):
    """The split is for the human at the gate. A review agent reading a file
    has no collapse to be defeated by, so its package keeps the one combined
    range (spec §1, "write_package is not changed")."""
    repo = make_repo(tmp_path)
    base = _head(repo)
    (repo / "doc.md").write_text("landed paperwork\n")
    config.git_read(repo, "add", "-A")
    subprocess.run(
        ["git", "commit", "-m", "land the doc"], cwd=repo, capture_output=True, check=True
    )
    (repo / "calc.py").write_text("in flight code\n")
    results = tmp_path / "results"
    results.mkdir()

    path = review.write_package(results, repo, base, "sess-span")
    assert path is not None
    body = path.read_text()
    assert "landed paperwork" in body
    assert "in flight code" in body
