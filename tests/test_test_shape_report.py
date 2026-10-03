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

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "test_shape_report.py"


def _load():
    spec = importlib.util.spec_from_file_location("_dev_test_shape_report", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def report():
    return _load()


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


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
    _write(tmp_path, "tests/test_a.py", _A)
    _write(tmp_path, "tests/test_b.py", _B)
    # the plugin's own testpath is not `tests/`: its tests stay out of every number
    _write(tmp_path, "plugins/kraft-lite/tests/test_p.py", "def test_p():\n    assert 1\n")
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
    _write(
        tree,
        "tests/test_c.py",
        "def _git(*args):\n    return list(args)\n\n\ndef test_c():\n    assert _git()\n",
    )
    helpers = report.measure(tree)["duplicated_helpers"]
    assert (helpers["names"], helpers["copies"]) == (1, 2)
    (group,) = helpers["groups"]
    assert group["name"] == "_git"
    assert group["files"] == ["tests/test_a.py", "tests/test_b.py"]


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
    _write(
        tmp_path,
        "tests/test_dup.py",
        "from collections.abc import Mapping\n" * 5 + "\n\n" + body,
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
    _write(tmp_path, "src/kraft/mod.py", big)
    _write(tmp_path, "src/kraft/mod_more.py", big)
    _write(tmp_path, "src/kraft/pkg/__init__.py", "")
    _write(tmp_path, "src/kraft/pkg/under.py", small)
    one = "def test_a():\n    assert 1\n"
    _write(tmp_path, "tests/test_mod.py", one + "\n\n" + one.replace("test_a", "test_b"))
    # `test_mod_more.py` mirrors `mod_more.py`, not `mod.py`
    _write(tmp_path, "tests/test_mod_more.py", one)
    # `test_mod_extra.py` has no module of its own, so it belongs to `mod`
    _write(tmp_path, "tests/test_mod_extra.py", one)
    _write(tmp_path, "tests/pkg/test_under.py", one)
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


def test_print_helper_allowlist_keys_by_name_and_fingerprint_hash(report, tree, capsys):
    assert report.main(["--root", str(tree), "--print-helper-allowlist"]) == 0
    printed = capsys.readouterr().out
    fingerprint = (
        "return subprocess.run(['git', *args], capture_output=True, check=True, text=True)"
    )
    key = f"_git#{hashlib.sha1(fingerprint.encode()).hexdigest()[:8]}"
    assert f'"{key}": 2' in printed


def test_a_file_that_does_not_parse_fails_the_report(report, tree, capsys):
    _write(tree, "tests/test_broken.py", "def test_x(:\n")
    assert report.main(["--root", str(tree)]) == 1
    assert "test_broken.py" in capsys.readouterr().out
