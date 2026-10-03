"""`dev/test_shape_report.py` prints the numbers the test-tree diet moves, so
these pin that each number counts what its label says -- on a tiny tree built
under `tmp_path`, never on the real `tests/`.

`measure(root)` is the seam: it takes a repo root (a `tests/` dir, and
optionally `src/kraft/`) and returns the numbers as a dict; `main` only prints
that dict, as text or as `--json`.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from support.harness import commit_all, git, write

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "test_shape_report.py"


def _load():
    spec = importlib.util.spec_from_file_location("_dev_test_shape_report", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def report():
    return _load()


# `_git` here and in test_b.py is the same helper written two ways: a
# docstring, annotations and keyword arguments in another order.
_A = """\
import subprocess

import pytest


def _git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def test_one():
    assert _git


@pytest.mark.parametrize("x", [1, 2, 3])
@pytest.mark.parametrize("y", ["a", "b", "c", "d"])
def test_two(x, y):
    assert x < y


@pytest.mark.parametrize("z", [1, 2])
class TestGroup:
    def test_three(self, z):
        assert True
"""

_B = '''\
import subprocess

import pytest

CASES = [1, 2, 3, 4]


def _git(*args: str) -> subprocess.CompletedProcess:
    """Run git."""
    return subprocess.run(["git", *args], text=True, capture_output=True, check=True)


@pytest.mark.parametrize("n", CASES)
async def test_four(n):
    assert n
'''


@pytest.fixture
def tree(tmp_path):
    write(tmp_path, "tests/test_a.py", _A)
    write(tmp_path, "tests/test_b.py", _B)
    # the plugin's own testpath is not `tests/`: its tests stay out of every number
    write(tmp_path, "plugins/kraft-lite/tests/test_p.py", "def test_p():\n    assert 1\n")
    return tmp_path


def test_counts_functions_and_parametrize_cases(report, tree):
    shape = report.measure(tree)
    assert shape["test_functions"] == 4
    # one + (3 x 4 stacked) + a class's 2 + a non-literal list counted as 2
    assert shape["collected_cases"] == 1 + 12 + 2 + 2
    assert shape["estimated_parametrize_sites"] == 1


def test_counts_a_helper_body_defined_in_two_files_once_per_file(report, tree):
    # a third file defines `_git` with a different body: its own group of one,
    # not a third copy of the duplicated one.
    write(
        tree,
        "tests/test_c.py",
        "def _git(*args):\n    return list(args)\n\n\ndef test_c():\n    assert _git()\n",
    )
    helpers = report.measure(tree)["duplicated_helpers"]
    assert (helpers["names"], helpers["copies"]) == (1, 2)
    (group,) = helpers["groups"]
    assert group["name"] == "_git"
    assert group["files"] == ["tests/test_a.py", "tests/test_b.py"]
    fingerprint = (
        "return subprocess.run(['git', *args], capture_output=True, check=True, text=True)"
    )
    assert group["key"] == f"_git#{hashlib.sha1(fingerprint.encode()).hexdigest()[:8]}"


def test_json_output_carries_the_same_numbers(report, tree, capsys):
    assert report.main(["--root", str(tree), "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == json.loads(json.dumps(report.measure(tree)))
    assert printed["test_functions"] == 4


def test_text_output_names_each_number(report, tree, capsys):
    assert report.main(["--root", str(tree)]) == 0
    out = capsys.readouterr().out
    assert "test functions          4" in out
    assert "collected cases (est.)  17" in out
    assert "duplicated helpers      1 names / 2 copies" in out
    assert "_git 2" in out


def test_verbatim_repeat_lines_need_five_sightings_and_real_length(report, tmp_path):
    long_line = "    result = compute_something(alpha, beta)"
    short_line = "    x = 1"
    body = "".join(
        f"def test_{i}():\n{long_line}\n{short_line}\n    assert result\n\n\n" for i in range(5)
    )
    # four sightings of `rare` stay under the bar; imports and comments never count.
    body += "# a comment long enough to pass the length floor\n" * 6
    body += "".join(
        f"def test_r{i}():\n    rare = compute_something_else(1)\n    assert rare\n\n\n"
        for i in range(4)
    )
    write(
        tmp_path, "tests/test_dup.py", "from collections.abc import Mapping\n" * 5 + "\n\n" + body
    )
    shape = report.measure(tmp_path)
    # only `result = compute_something(alpha, beta)` x5 qualifies: `x = 1` is
    # under the length floor, `assert result` too, the imports are skipped.
    assert shape["verbatim_repeat_lines"] == 5
    assert shape["nontrivial_lines"] > 5


def test_densest_modules_rank_by_tests_per_100_code_lines(report, tmp_path):
    big = '"""Docstring\nover lines."""\n# a comment\n\n' + "".join(
        f"x{i} = {i}\n" for i in range(150)
    )
    small = "".join(f"y{i} = {i}\n" for i in range(149))
    write(tmp_path, "src/kraft/mod.py", big)
    write(tmp_path, "src/kraft/mod_more.py", big)
    write(tmp_path, "src/kraft/pkg/__init__.py", "")
    write(tmp_path, "src/kraft/pkg/under.py", small)
    one = "def test_a():\n    assert 1\n"
    write(tmp_path, "tests/test_mod.py", one + "\n\n" + one.replace("test_a", "test_b"))
    # `test_mod_more.py` mirrors `mod_more.py`, not `mod.py`
    write(tmp_path, "tests/test_mod_more.py", one)
    # `test_mod_extra.py` has no module of its own, so it belongs to `mod`
    write(tmp_path, "tests/test_mod_extra.py", one)
    write(tmp_path, "tests/pkg/test_under.py", one)
    modules = {m["module"]: m for m in report.measure(tmp_path)["densest_modules"]}
    assert set(modules) == {"mod.py", "mod_more.py"}  # `pkg/under.py` has 149 code lines
    assert (modules["mod.py"]["tests"], modules["mod.py"]["code_lines"]) == (3, 150)
    assert modules["mod.py"]["per_100"] == 2.0
    assert modules["mod_more.py"]["tests"] == 1


def test_fingerprint_ignores_docstring_annotations_and_keyword_order(report):
    def fn(source):
        return report.helper_fingerprint(ast.parse(source).body[0])

    plain = "def f(x):\n    def g(y):\n        return h(y, a=1, b=2)\n    return g(x)\n"
    dressed = (
        'def f(x: int) -> int:\n    """Doc."""\n'
        "    def g(y: str):\n        return h(y, b=2, a=1)\n    return g(x)\n"
    )
    assert fn(plain) == fn(dressed)
    assert fn(plain) != fn(plain.replace("b=2", "b=3"))


def test_fingerprint_appends_each_module_constant_the_body_names(report):
    def fn(source):
        module = ast.parse(source)
        return report.helper_fingerprint(module.body[-1], module)

    row = "def _row(**f):\n    return {**DEFAULTS, **f}\n"
    one, two = "DEFAULTS = {'a': 1}\n" + row, "DEFAULTS = {'a': 2}\n" + row
    assert fn(one) != fn(two)
    assert fn(one) == fn("import os\n" + one)
    assert fn(one).endswith("DEFAULTS = {'a': 1}")
    # an imported or unknown name adds nothing past the name already in the body,
    # nor does one a top-level statement only assigns into
    imported = "from defaults import DEFAULTS\n" + row
    assert fn(imported) == report.helper_fingerprint(ast.parse(row).body[0])
    env = "def _env():\n    return os.environ['HOME']\n"
    patched = "import os\nos.environ['HOME'] = '/h'\n" + env
    assert fn(patched) == report.helper_fingerprint(ast.parse(env).body[0])
    # several constants: sorted by name, not by where the body names them
    assert fn("A = 1\nB = 2\n\ndef f():\n    return B + A\n").endswith("\nA = 1\nB = 2")


@pytest.mark.parametrize(
    ("binding", "appended"),
    [
        ("DEFAULTS = {'a': 1}", "DEFAULTS = {'a': 1}"),
        ("DEFAULTS: dict = {'a': 1}", "DEFAULTS = {'a': 1}"),
        ("OTHER, DEFAULTS = 0, {'a': 1}", "DEFAULTS = (0, {'a': 1})"),
        ("OTHER = DEFAULTS = {'a': 1}", "DEFAULTS = {'a': 1}"),
        ("DEFAULTS: dict\nDEFAULTS = {'a': 1}", "DEFAULTS = {'a': 1}"),
        ("DEFAULTS = {}\nDEFAULTS = {'a': 1}", "DEFAULTS = {}\nDEFAULTS = {'a': 1}"),
    ],
    ids=["plain", "annotated", "unpacked", "chained", "declared", "rebound"],
)
def test_fingerprint_reads_each_top_level_binding_shape(report, binding, appended):
    module = ast.parse(binding + "\n\n\ndef _row(**f):\n    return {**DEFAULTS, **f}\n")
    assert report.helper_fingerprint(module.body[-1], module) == (
        "return {**DEFAULTS, **f}\n" + appended
    )


def test_helpers_over_different_module_constants_are_not_one_group(report, tmp_path):
    row = "def _row(**f):\n    return {**DEFAULTS, **f}\n\n\ndef test_r():\n    assert _row()\n"
    write(tmp_path, "tests/test_a.py", "DEFAULTS = {'a': 1}\n" + row)
    write(tmp_path, "tests/test_b.py", "DEFAULTS = {'b': 2}\n" + row)
    assert report.measure(tmp_path)["duplicated_helpers"]["names"] == 0
    write(tmp_path, "tests/test_c.py", "DEFAULTS = {'a': 1}\n" + row)
    assert report.measure(tmp_path)["duplicated_helpers"]["groups"][0]["files"] == [
        "tests/test_a.py",
        "tests/test_c.py",
    ]


def test_print_helper_ceiling_prints_rule_e_numbers_over_both_testpaths(report, tree, capsys):
    # a second duplicated group, so the two numbers differ, and a copy of it in the
    # plugin's testpath: out of the shape report's numbers, in rule (e)'s
    row = "def _row():\n    return 1\n"
    for name in ("test_a", "test_b", "test_c"):
        write(tree, f"tests/{name}_rows.py", row)
    write(tree, "plugins/kraft-lite/tests/test_p_rows.py", row)
    helpers = report.measure(tree)["duplicated_helpers"]
    assert (helpers["names"], helpers["copies"]) == (2, 5)
    assert report.main(["--root", str(tree), "--print-helper-ceiling"]) == 0
    assert capsys.readouterr().out == 'DUPLICATE_HELPER_CEILING = {"groups": 2, "copies": 6}\n'


def test_a_file_that_does_not_parse_fails_the_report(report, tree, capsys):
    write(tree, "tests/test_broken.py", "def test_x(:\n")
    assert report.main(["--root", str(tree)]) == 1
    assert "test_broken.py" in capsys.readouterr().out


# -- `--diff BASE HEAD`: the delta a pull request makes to the tree ----------

_COMMIT = '    return subprocess.run(["git", "commit", "-qm", "x"], cwd=repo, check=True)'
_REPEATED = "    result = compute_something(alpha, beta)"
_PAIR = "    pair = compute_pair(alpha, beta)"


def _plain(prefix: str, count: int, line: str = _REPEATED) -> str:
    body = f"():\n{line}\n    assert result\n\n\n"
    return "".join(f"def test_{prefix}{i}{body}" for i in range(count))


def _commit_helper() -> str:
    return f"def _commit(repo):\n{_COMMIT}\n\n\n"


# BASE: `_REPEATED` sits in five tests (so it is a verbatim repeat there), thirty
# other long lines are each seen once, and `_commit` has a copy in a test file
# and the shared one under `tests/support`, and `_PAIR` is seen twice: 39 non-trivial
# lines, 5 of them repeats.
_BASE = {
    "tests/test_a.py": (
        "import pytest\nimport subprocess\n\n\n"
        + _commit_helper()
        + _plain("a", 5)
        + f"def test_pair():\n{_PAIR}\n{_PAIR}\n    assert pair\n\n\n"
        + "def test_unique():\n"
        + "".join(f"    value_{i} = unique_lookup_{i}(argument_{i})\n" for i in range(30))
        + "    assert value_0\n\n\n"
        + '@pytest.mark.parametrize("n", [1, 2])\ndef test_rows(n):\n    assert n\n'
    ),
    "tests/support/__init__.py": "",
    "tests/support/harness.py": "import subprocess\n\n\ndef commit_all(repo):\n" + _COMMIT + "\n",
}


def _git_repo(root: Path, files: dict[str, str | None]) -> str:
    """Commit `files` over whatever `root` holds (`None` deletes one) and return
    the new commit's sha."""
    if not (root / ".git").exists():
        git(root, "init", "-q", "-b", "main")
    for rel, text in files.items():
        if text is None:
            (root / rel).unlink()
        else:
            write(root, rel, text)
    return commit_all(root)


def _nudge(report, tmp_path, capsys, head_files: dict[str, str | None]) -> tuple[str, int]:
    base = _git_repo(tmp_path, _BASE)
    head = _git_repo(tmp_path, head_files)
    code = report.main(["--diff", base, head, "--root", str(tmp_path)])
    return capsys.readouterr().out, code


def test_diff_mode_reports_the_delta_between_two_revisions(report, tmp_path, capsys):
    new_file = "import subprocess\n\n\n" + _commit_helper() + _plain("b", 3)
    out, code = _nudge(report, tmp_path, capsys, {"tests/test_b.py": new_file})
    assert code == 0
    assert out == (
        "This PR changes the test tree:\n"
        "```\n"
        "  test functions     +3    (0 of them parametrized)\n"
        "  collected cases    +3\n"
        f"  lines in tests/    +{len(new_file.splitlines())}\n"
        "  verbatim-repeat    +3 lines already present 5+ times elsewhere in tests/\n"
        "  helpers            +1 copy of `_commit` (3 exist; "
        "`support.harness.commit_all` is the shared one)\n"
        "```\n"
        "If the new tests are rows of one behavior, fold them into a table\n"
        '(docs/testing.md, "One behaviour, one test"). If they are new behaviors, ignore this.\n'
    )


def test_diff_mode_is_quiet_when_there_is_nothing_to_nudge_about(report, tmp_path, capsys):
    # one more row of the existing parametrized test: no new function, and the
    # one added line is not a repeat of anything.
    row = _BASE["tests/test_a.py"].replace("[1, 2]", "[1, 2, 3]")
    out, code = _nudge(report, tmp_path, capsys, {"tests/test_a.py": row})
    assert (out, code) == ("nothing to nudge about\n", 0)


def _file(*parts: str) -> str:
    return "import pytest\nimport subprocess\n\n\n" + "".join(parts)


_ONE_PARAMETRIZED = '@pytest.mark.parametrize("n", [1])\ndef test_p(n):\n    assert n\n'


@pytest.mark.parametrize(
    ("where", "added", "says"),
    [
        pytest.param(
            "tests/test_b.py",
            _file(_plain("b", 3)),
            "+3    (0 of them parametrized)",
            id="three-plain",
        ),
        pytest.param("tests/test_b.py", _file(_plain("b", 2)), None, id="two-plain"),
        pytest.param(
            "tests/test_b.py",
            _file(_plain("b", 2), _ONE_PARAMETRIZED),
            None,
            id="three-one-parametrized",
        ),
        pytest.param(
            "tests/test_b.py",
            _file(_commit_helper(), "def test_b():\n    assert _commit\n"),
            "+1 copy of `_commit` (3 exist; `support.harness.commit_all` is the shared one)",
            id="helper-copy",
        ),
        pytest.param(
            "tests/support/more.py",
            _file("def commit_all(repo):\n" + _COMMIT + "\n"),
            None,
            id="helper-copy-under-support",
        ),
        pytest.param(
            "tests/test_b.py",
            _file("def test_b():\n" + (_REPEATED + "\n") * 20 + "    assert 1\n"),
            "+20 lines already present 5+ times",
            id="repeat-share-over-the-trees",
        ),
        pytest.param(
            "tests/test_b.py",
            _file("def test_b():\n" + (_REPEATED + "\n") * 3 + "    assert 1\n"),
            None,
            id="a-handful-of-repeats-is-not-a-share",
        ),
        pytest.param(
            "tests/test_b.py",
            _file(
                "def test_b():\n" + (_REPEATED + "\n") * 2,
                "".join(f"    value_b{i} = unique_lookup_b{i}(argument_b{i})\n" for i in range(18)),
                "    assert 1\n",
            ),
            None,
            id="repeats-below-the-trees-own-share",
        ),
        pytest.param(
            "tests/test_b.py",
            _file("def test_b():\n" + (_PAIR + "\n") * 20 + "    assert 1\n"),
            None,
            id="a-line-seen-twice-in-base-is-not-a-repeat-however-often-the-pr-adds-it",
        ),
        pytest.param(
            "tests/test_a.py",
            _BASE["tests/test_a.py"]
            .replace("_a0():", "_a0(x=1):")
            .replace("_a1():", "_a1(x=1):")
            .replace("_a2():", "_a2(x=1):"),
            None,
            id="three-edited-signatures-are-not-three-new-tests",
        ),
        pytest.param(
            "tests/test_b.py",
            _file(
                '@pytest.mark.parametrize("n", [1, 2])\nclass TestRows:\n',
                *(f"    def test_row{i}(self, n):\n        assert n\n\n" for i in range(3)),
            ),
            None,
            id="three-methods-of-a-parametrized-class",
        ),
    ],
)
def test_diff_mode_nudges_only_on_one_of_its_three_signs(
    report, tmp_path, capsys, where, added, says
):
    out, code = _nudge(report, tmp_path, capsys, {where: added})
    assert code == 0
    if says is None:
        assert out == "nothing to nudge about\n"
    else:
        assert says in out


def test_diff_mode_follows_a_moved_file_instead_of_calling_it_all_new(report, tmp_path, capsys):
    moved = {"tests/test_a.py": None, "tests/test_c.py": _BASE["tests/test_a.py"]}
    out, code = _nudge(report, tmp_path, capsys, moved)
    assert (out, code) == ("nothing to nudge about\n", 0)


def _worktrees(root: Path) -> int:
    return git(root, "worktree", "list", "--porcelain").count("worktree ")


def test_diff_mode_exits_0_and_leaves_no_worktree_behind_when_it_cannot_measure(
    report, tmp_path, capsys, monkeypatch
):
    base = _git_repo(tmp_path, _BASE)
    head = _git_repo(tmp_path, {"tests/test_b.py": _file(_plain("b", 3))})

    assert report.main(["--diff", base, "no-such-rev", "--root", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "nothing to nudge about\n"
    assert "tests nudge skipped" in captured.err

    def boom(root):
        raise OSError("disk went away")

    monkeypatch.setattr(report, "measure", boom)
    assert report.main(["--diff", base, head, "--root", str(tmp_path)]) == 0
    assert "disk went away" in capsys.readouterr().err
    assert _worktrees(tmp_path) == 1
