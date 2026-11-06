"""Check by experiment that `dev/targeted_tests.py` names every test a change can break.

`targeted_tests.py` runs named tests, not the full suite, for some changes
testmon cannot see (a comment-only `config/` change, an edit under a mapped
prefix). Its lists come from tracing readers, and a reader missed there is a
test skipped silently. This makes such a change for real and runs the suite:

    check_targeted_maps.py config [-n N]

In a throwaway worktree at HEAD it runs the full suite once as it is (the
baseline: failures this machine has anyway), applies the change, asks
`targeted_tests.plan()` what it would run, and refuses if the plan would run
the full suite (the change would not exercise the lists). Then it runs the full
suite again. A test that fails now but not in the baseline, and that the plan
did not name, is a reader the lists miss: it is printed, and the exit is 1.

Two full runs: about 40 minutes on four CPUs. Not run by CI.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_MARK = "check_targeted_maps"


def _targeted():
    spec = importlib.util.spec_from_file_location(
        "_targeted_tests", ROOT / "dev" / "targeted_tests.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # a dataclass resolves its annotations here
    spec.loader.exec_module(mod)
    return mod


def _load(path: str, text: str) -> object:
    if path.endswith(".json"):
        return json.loads(text)
    import yaml

    return yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))


def comment_everywhere(path: str, text: str) -> str:
    """`text` with a whole-line comment above every line, at that line's own
    indent, and a blank line above every third, wherever the data stays equal
    (not inside a block scalar, where either is text). JSON takes blank lines
    only. The last line keeps its place, so the end of the file is unchanged."""
    want = _load(path, text)
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    for i, line in enumerate(lines):
        extra = []
        if not path.endswith(".json") and line.strip():
            indent = line[: len(line) - len(line.lstrip())]
            extra.append(f"{indent}# {_MARK}: an injected comment\n")
        if i % 3 == 1:
            extra.append("\n")
        for add in extra:
            try:
                same = _load(path, "".join([*out, add, *lines[i:]])) == want
            except Exception:  # a parse error: that place cannot take it
                same = False
            if same:
                out.append(add)
        out.append(line)
    return "".join(out)


def perturb_config(root: Path, tt) -> list[str]:
    """Comment every YAML and JSON file under the comment trees."""
    changed = []
    for tree in tt.COMMENT_TREES:
        top = root / tree
        files = [top] if top.is_file() else sorted(p for p in top.rglob("*") if p.is_file())
        for file in files:
            rel = file.relative_to(root).as_posix()
            if not rel.endswith(tt._STRUCTURED):
                continue
            text = file.read_text()
            after = comment_everywhere(rel, text)
            if after != text:
                file.write_text(after)
                changed.append(rel)
    return changed


#: Every marker and field the fake agents steer by (fixtures/fake-claude.sh,
#: tests/support/fake_agent.py), save KRAFT_HANG and KRAFT_SLOW, which would
#: only wait: a skill that said any of them would steer every chain-walking test
#: if the fakes read a method.
STEERING = (
    "\n\nKRAFT_FAIL KRAFT_SUBAGENTS KRAFT_MANY_FILES KRAFT_COST_9.99\n"
    "Hook point: check_targeted_maps\n"
    "Node: check_targeted_maps\n"
    "Write your notes to /nonexistent/check_targeted_maps.md, creating that directory.\n"
    "Write your session summary to /nonexistent/summary.md, starting with it.\n"
    "replayed onto 0000000\n"
)


def perturb_skills(root: Path, tt) -> list[str]:
    """Append the fakes' markers and fields to every skill Kraft ships."""
    changed = []
    for file in sorted((root / "src/kraft/skills").glob("*/SKILL.md")):
        file.write_text(file.read_text() + STEERING)
        changed.append(file.relative_to(root).as_posix())
    return changed


PERTURB: dict[str, Callable[[Path, object], list[str]]] = {
    "config": perturb_config,
    "skills": perturb_skills,
}

_FAILED = re.compile(r"^(?:FAILED|ERROR) (\S+?)(?: - .*)?$", re.M)


def failures(output: str) -> set[str]:
    """The node ids pytest's short summary (`-rfE`) names as failed or errored."""
    return set(_FAILED.findall(output))


def named_tests(plan) -> list[str]:
    """The tests the plan's first step names: what it adds beyond testmon's picks."""
    argv = plan.steps[0].argv
    return argv[3 : argv.index("--no-testmon")]


def uncovered(new: Iterable[str], named: Iterable[str]) -> list[str]:
    """The failures no named test file or directory holds."""
    named = [n.rstrip("/") for n in named]
    return sorted(
        node
        for node in new
        if not any((file := node.split("::", 1)[0]) == n or file.startswith(f"{n}/") for n in named)
    )


def missed(baseline: set[str], perturbed: set[str], named: Iterable[str]) -> list[str]:
    """What failed only with the change, in no test the plan named."""
    return uncovered(perturbed - baseline, named)


def _suite(root: Path, workers: str, *nodes: str) -> set[str]:
    env = {**os.environ, "PYTHONPATH": str(root / "src")}  # this tree's kraft, not the install's
    argv = [sys.executable, "-m", "pytest", *nodes, "-m", "not e2e", "-n", workers]
    argv += ["--no-testmon", "-p", "no:cacheprovider", "-q", "-rfE"]
    done = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, check=False)
    return failures(done.stdout)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tree", choices=sorted(PERTURB))
    ap.add_argument("-n", dest="workers", help="xdist workers (default: as test-targeted)")
    args = ap.parse_args(argv)
    tt = _targeted()
    workers = args.workers or tt.workers(os.environ)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="check-targeted-maps-") as tmp:
        root = Path(tmp) / "tree"
        subprocess.run(
            ["git", "worktree", "add", "-q", "--detach", str(root), head], cwd=ROOT, check=True
        )
        try:
            print(f"{args.tree}: baseline run at {head[:12]}", flush=True)
            baseline = _suite(root, workers)
            changed = PERTURB[args.tree](root, tt)
            plan = tt.plan(changed, head, root)
            if plan.reason or not changed:
                print(
                    f"{args.tree}: the change would run the full suite, so it checks nothing: "
                    f"{plan.reason or 'nothing changed'}",
                    file=sys.stderr,
                )
                return 2
            named = named_tests(plan)
            print(
                f"{args.tree}: changed {len(changed)} files; the plan names {len(named)}",
                flush=True,
            )
            perturbed = _suite(root, workers)
            lost = missed(baseline, perturbed, named)
            if lost:  # once more, so a flaky test is not taken for a missed reader
                lost = missed(baseline, _suite(root, workers, *lost), named)
            new = perturbed - baseline
            print(
                f"{args.tree}: {len(baseline)} baseline failures, {len(new)} new, "
                f"{len(new) - len(lost)} of them named"
            )
            for node in sorted(new):
                print(f"  {'NOT NAMED' if node in lost else 'named'}: {node}")
            return 1 if lost else 0
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(root)], cwd=ROOT, check=False
            )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
