"""`dev/check_tests.py` is itself unexercised by the suite it checks -- these
pin its five rules against a deliberately bad file each, the staleness rules
that keep (c), (d) and (e)'s exceptions from rotting into a one-way ratchet,
plus the "a parse failure is a failure" rule the project keeps re-learning
the hard way (a checker that reads an error as "nothing to check" is the
same bug class as a chain step that swallows an exception into "done").

Most of the checker's functions take a tree and a `relpath` string
directly, so a case only needs a parsed module, not a file on disk under
the real tests/ tree (which `just test`'s own collector would then try to
run as tests). The line-budget and parse-failure cases do need a real file,
so those use `tmp_path`.
"""

from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path

import pytest
from support.harness import write

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "check_tests.py"


def _load():
    spec = importlib.util.spec_from_file_location("_dev_check_tests", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ct():
    return _load()


def _tree(source: str) -> ast.Module:
    return ast.parse(source)


class TestE2eNamesCli:
    def test_a_zero_arg_e2e_call_is_flagged(self, ct):
        tree = _tree("import pytest\n\n@pytest.mark.e2e()\ndef test_x():\n    assert True\n")
        violations = ct.check_e2e_names_cli(tree, "test_bad.py")
        assert len(violations) == 1
        assert "needs at least one" in violations[0]

    def test_a_named_cli_is_not_flagged(self, ct):
        tree = _tree('import pytest\n\n@pytest.mark.e2e("bd")\ndef test_x():\n    assert True\n')
        assert ct.check_e2e_names_cli(tree, "test_ok.py") == []

    def test_a_bare_marker_with_no_call_is_flagged(self, ct):
        tree = _tree("import pytest\n\npytestmark = pytest.mark.e2e\n")
        violations = ct.check_e2e_names_cli(tree, "test_bad.py")
        assert len(violations) == 1
        assert "no call and no" in violations[0]


class TestNoRealCliOutsideE2e:
    def test_an_unmarked_test_shelling_out_to_bd_is_flagged(self, ct):
        tree = _tree(
            "import subprocess\n\n"
            "def test_reaches_real_bd():\n"
            '    subprocess.run(["bd", "status"], check=True)\n'
        )
        violations = ct.check_no_real_cli_outside_e2e(tree, "test_bad.py")
        assert len(violations) == 1
        assert "bd" in violations[0]

    def test_an_e2e_marked_test_is_exempt(self, ct):
        tree = _tree(
            "import subprocess\nimport pytest\n\n"
            '@pytest.mark.e2e("bd")\n'
            "def test_reaches_real_bd():\n"
            '    subprocess.run(["bd", "status"], check=True)\n'
        )
        assert ct.check_no_real_cli_outside_e2e(tree, "test_ok.py") == []

    def test_a_git_call_is_never_flagged(self, ct):
        """`git` is real everywhere on purpose -- only the four guarded
        binaries trip this rule."""
        tree = _tree(
            "import subprocess\n\n"
            "def test_makes_a_repo():\n"
            '    subprocess.run(["git", "init"], check=True)\n'
        )
        assert ct.check_no_real_cli_outside_e2e(tree, "test_ok.py") == []

    def test_a_test_decorated_with_an_unrelated_marker_is_still_flagged(self, ct):
        """Regression: `_decorator_list_is_e2e` used to call `_e2e_sites`
        (a generator function) directly inside `any(...)`, checking the
        truthiness of the generator *objects* it produced rather than what
        they yielded -- a generator object is always truthy, so any
        decorated test at all, `@pytest.mark.parametrize` included, read as
        e2e-exempt regardless of what the decorator actually was."""
        tree = _tree(
            "import subprocess\nimport pytest\n\n"
            '@pytest.mark.parametrize("x", [1, 2])\n'
            "def test_x(x):\n"
            '    subprocess.run(["bd", "status"])\n'
        )
        violations = ct.check_no_real_cli_outside_e2e(tree, "test_bad.py")
        assert len(violations) == 1
        assert "bd" in violations[0]

    @pytest.mark.parametrize(
        ("flagged", "clean"),
        [
            (
                'subprocess.run(args=["claude", "-p"])',
                'subprocess.run(args=["git", "status"])',
            ),
            (
                'subprocess.run("claude -p hi", shell=True)',
                'subprocess.run("git log && echo claude", shell=True)',
            ),
            (
                'subprocess.run(["sh", "-c", "cd x && codex exec"])',
                'subprocess.run(["sh", "-c", "echo codex > out"])',
            ),
            (
                'subprocess.run(["/usr/bin/env", "FOO=1", "gemini"])',
                'subprocess.run(["/usr/bin/env", "python3", "gemini.py"])',
            ),
            (
                'cmd = ["opencode", "run"]\n    subprocess.run(cmd)',
                'cmd = ["git", "log"]\n    subprocess.run(cmd)',
            ),
            (
                'def _argv():\n        return ["amp", "-x"]\n    subprocess.Popen(_argv())',
                'def _argv():\n        return ["git", "-x"]\n    subprocess.Popen(_argv())',
            ),
            (
                'asyncio.create_subprocess_exec("agy", "--version")',
                'asyncio.create_subprocess_exec("git", "agy")',
            ),
            (
                'asyncio.create_subprocess_exec(*["cursor-agent", "-p"])',
                'asyncio.create_subprocess_exec(*["git", "cursor-agent"])',
            ),
            ('os.system("glab mr list 2>/dev/null")', 'os.system("git log > glab")'),
            ('forge.run_git(repo, ["gh", "pr", "view"])', 'forge.run_git(repo, ["git", "log"])'),
        ],
        ids=[
            "args-keyword",
            "shell-string",
            "sh-c",
            "env-wrapper",
            "variable-argv",
            "helper-return",
            "create-subprocess-exec",
            "starred-exec",
            "os-system",
            "run-git",
        ],
    )
    def test_an_evasion_shape_is_flagged_and_its_lookalike_is_not(self, ct, flagged, clean):
        """Each road to a guarded CLI the checker once let through, beside the
        same call aimed at `git` or naming the CLI only as an argument."""
        for body, expected in ((flagged, 1), (clean, 0)):
            tree = _tree(f"def test_x(repo):\n    {body}\n")
            violations = ct.check_no_real_cli_outside_e2e(tree, "test_bad.py")
            assert len(violations) == expected, (body, violations)

    @pytest.mark.parametrize("binary", sorted({"bd", "codex", "gemini", "opencode", "amp"}))
    def test_every_guarded_cli_is_named(self, ct, binary):
        tree = _tree(f'def test_x():\n    subprocess.run(["{binary}", "x"])\n')
        assert len(ct.check_no_real_cli_outside_e2e(tree, "test_bad.py")) == 1

    def test_the_names_match_the_runtime_guard(self, ct):
        """The static list and the stubs the runtime puts on PATH are one set,
        plus `bd`, which the runtime fakes at its adapter instead."""
        from support.real_binaries import GUARDED_BINARIES

        assert ct.GUARDED_BINARIES == {"bd"} | GUARDED_BINARIES

    def test_module_level_code_is_checked(self, ct):
        """A table of lambdas runs for whichever test reads it."""
        tree = _tree('PROBES = {"x": lambda: subprocess.run(["claude", "-v"])}\n')
        assert len(ct.check_no_real_cli_outside_e2e(tree, "test_bad.py")) == 1
        tree = _tree('PROBES = {"x": lambda: subprocess.run(["git", "-v"])}\n')
        assert ct.check_no_real_cli_outside_e2e(tree, "test_ok.py") == []

    def test_an_allowlisted_file_is_exempt_until_it_has_nothing_to_exempt(self, ct):
        relpath = next(iter(ct.REAL_CLI_ALLOWLIST))
        tree = _tree('def test_x():\n    subprocess.run(["claude", "-v"])\n')
        assert ct.check_no_real_cli_outside_e2e(tree, relpath) == []
        assert ct.check_real_cli_allowlist_is_current({relpath: 1}) == []
        (stale,) = ct.check_real_cli_allowlist_is_current({relpath: 0})
        assert "reaches no guarded CLI" in stale
        (gone,) = ct.check_real_cli_allowlist_is_current({})
        assert "no longer exists" in gone

    def test_support_helpers_are_out_of_scope_by_filename(self, ct):
        """tests/support/** is fixture infrastructure, not a collected test
        module -- the real-CLI half of a dual-tier fixture (like
        support.fake_beads.Bd._cli) lives there on purpose."""
        tree = _tree(
            'import subprocess\n\ndef _cli():\n    subprocess.run(["bd", "show"], check=True)\n'
        )
        assert ct.check_no_real_cli_outside_e2e(tree, "support/fake_beads.py") == []


class TestLineBudget:
    def test_a_file_over_budget_and_not_allowlisted_is_flagged(self, ct, tmp_path):
        path = tmp_path / "test_huge.py"
        path.write_text("\n" * (ct.LINE_BUDGET + 1))
        violations = ct.check_line_budget(path, "tests/test_huge.py")
        assert len(violations) == 1
        assert "LINE_BUDGET_ALLOWLIST" in violations[0]

    def test_a_file_under_budget_is_not_flagged(self, ct, tmp_path):
        path = tmp_path / "test_small.py"
        path.write_text("def test_x():\n    assert True\n")
        assert ct.check_line_budget(path, "tests/test_small.py") == []

    def test_an_allowlisted_file_may_not_grow_past_its_recorded_size(self, ct, tmp_path):
        relpath = next(iter(ct.LINE_BUDGET_ALLOWLIST))
        ceiling = ct.LINE_BUDGET_ALLOWLIST[relpath]
        path = tmp_path / "test_over_ceiling.py"
        path.write_text("\n" * (ceiling + 1))
        violations = ct.check_line_budget(path, relpath)
        assert len(violations) == 1
        assert "allowlisted ceiling" in violations[0]


class TestLineBudgetAllowlistIsCurrent:
    def test_an_entry_for_a_missing_file_is_flagged(self, ct):
        violations = ct.check_line_budget_allowlist_is_current({})
        assert len(violations) == len(ct.LINE_BUDGET_ALLOWLIST)
        assert all("no longer exists" in v for v in violations)

    def _all_present(self, ct) -> dict[str, int]:
        """Every allowlisted entry present, at exactly its recorded ceiling --
        a baseline that passes on its own, so a test can override just the
        one entry it's exercising without the others reporting `None` (a
        `check_line_budget_allowlist_is_current({relpath: N})` call would
        otherwise "discover" every unlisted entry missing)."""
        return dict(ct.LINE_BUDGET_ALLOWLIST)

    def test_an_entry_at_or_under_budget_is_stale(self, ct):
        relpath = next(iter(ct.LINE_BUDGET_ALLOWLIST))
        actual = self._all_present(ct)
        actual[relpath] = ct.LINE_BUDGET
        violations = ct.check_line_budget_allowlist_is_current(actual)
        assert len(violations) == 1
        assert "stale" in violations[0]

    def _biggest_entry(self, ct) -> str:
        """A ceiling with enough headroom above LINE_BUDGET that subtracting
        the margin still lands well clear of the "at or under budget" branch
        -- so this test exercises the margin rule specifically, not the
        stale-budget one above it."""
        return max(ct.LINE_BUDGET_ALLOWLIST, key=ct.LINE_BUDGET_ALLOWLIST.get)

    def test_a_ceiling_too_far_above_actual_is_flagged(self, ct):
        relpath = self._biggest_entry(ct)
        ceiling = ct.LINE_BUDGET_ALLOWLIST[relpath]
        actual = self._all_present(ct)
        actual[relpath] = ceiling - ct.STALE_ALLOWLIST_MARGIN - 1
        violations = ct.check_line_budget_allowlist_is_current(actual)
        assert len(violations) == 1
        assert "tighten it" in violations[0]

    def test_a_ceiling_within_the_margin_is_not_flagged(self, ct):
        relpath = self._biggest_entry(ct)
        ceiling = ct.LINE_BUDGET_ALLOWLIST[relpath]
        actual = self._all_present(ct)
        actual[relpath] = ceiling - ct.STALE_ALLOWLIST_MARGIN
        assert ct.check_line_budget_allowlist_is_current(actual) == []

    def test_every_real_entry_passes_today(self, ct):
        """The live-fire version: today's allowlist against today's tree."""
        actual = {p: ct._line_count(ct.ROOT / p) for p in ct.LINE_BUDGET_ALLOWLIST}
        assert ct.check_line_budget_allowlist_is_current(actual) == []


class TestExpectationAllowlistIsCurrent:
    def test_a_missing_test_id_is_flagged(self, ct):
        violations = ct.check_expectation_allowlist_is_current(set())
        assert len(violations) == len(ct.EXPECTATION_ALLOWLIST)
        assert all("no longer exists" in v for v in violations)

    def test_every_real_entry_is_present_today(self, ct):
        assert ct.check_expectation_allowlist_is_current(ct.EXPECTATION_ALLOWLIST) == []


class TestEveryTestHasAnExpectation:
    def test_a_test_with_no_assert_is_flagged(self, ct):
        tree = _tree("def test_x():\n    1 + 1\n")
        violations = ct.check_every_test_has_an_expectation(tree, "test_bad.py")
        assert len(violations) == 1
        assert "no assert" in violations[0]

    def test_a_test_with_an_assert_is_not_flagged(self, ct):
        tree = _tree("def test_x():\n    assert 1 + 1 == 2\n")
        assert ct.check_every_test_has_an_expectation(tree, "test_ok.py") == []

    def test_a_test_delegating_to_a_same_module_helper_is_not_flagged(self, ct):
        tree = _tree("def _helper():\n    assert 1 + 1 == 2\n\n\ndef test_x():\n    _helper()\n")
        assert ct.check_every_test_has_an_expectation(tree, "test_ok.py") == []

    @pytest.mark.parametrize(
        ("vacuous", "real"),
        [
            ("assert True", "assert ok"),
            ("assert ok or True", "assert ok or other"),
            ('assert (ok, "message")', 'assert ok, "message"'),
            (
                "with pytest.raises(Exception):\n        go()",
                'with pytest.raises(Exception, match="refused"):\n        go()',
            ),
            (
                "with pytest.raises(BaseException):\n        go()",
                "with pytest.raises(ValueError):\n        go()",
            ),
            ("if False:\n        assert ok", "if ok:\n        assert other"),
            ("if True:\n        pass\n    else:\n        assert ok", "if True:\n        assert ok"),
            (
                "def never():\n        assert ok\n    go()",
                "def check():\n        assert ok\n    check()",
            ),
        ],
        ids=[
            "assert-true",
            "or-true",
            "assert-tuple",
            "raises-exception",
            "raises-baseexception",
            "if-false",
            "dead-else",
            "nested-def-never-called",
        ],
    )
    def test_a_vacuous_expectation_does_not_count(self, ct, vacuous, real):
        """Each passes whatever the code does; beside it, the same shape that
        can fail does count."""
        for body, expected in ((vacuous, 1), (real, 0)):
            tree = _tree(f"def test_x(ok, other):\n    {body}\n")
            violations = ct.check_every_test_has_an_expectation(tree, "test_x.py")
            assert len(violations) == expected, (body, violations)

    def test_a_nested_def_handed_on_as_a_callback_counts(self, ct):
        """A fake that asserts on what the code calls it with runs when the
        code does, so a test that passes it on has an expectation."""
        tree = _tree(
            "def test_x(monkeypatch):\n"
            "    def fake(arg):\n"
            "        assert arg == 1\n"
            "    monkeypatch.setattr(mod, 'f', fake)\n"
            "    mod.go()\n"
        )
        assert ct.check_every_test_has_an_expectation(tree, "test_x.py") == []

    def test_an_allowlisted_test_is_not_flagged_even_with_no_assert(self, ct):
        relpath, name = next(iter(ct.EXPECTATION_ALLOWLIST)).split("::")
        tree = _tree(f"def {name}():\n    1 + 1\n")
        assert ct.check_every_test_has_an_expectation(tree, relpath) == []


def _parsed(files: dict[str, str]) -> dict[str, ast.Module]:
    """Each `relpath: source` parsed, keyed by relpath."""
    return {relpath: ast.parse(source) for relpath, source in files.items()}


# `_git` sits on line 4 of every file built from this.
_GIT = (
    "import subprocess\n\n\n"
    "def _git(*args):\n"
    '    return subprocess.run(["git", *args], check=True, text=True)\n'
)
_MOVE_IT = "-- move it into tests/support/ and import it"
_ZERO = {"groups": 0, "copies": 0}
_NESTED = "def f(x):\n    def g(y):\n        return h(y, a=1, b=2)\n    return g(x)\n"
_ROW = "\n\ndef _row(**f):\n    return {**DEFAULTS, **f}\n"
_ENV = "\n\ndef _env():\n    return os.environ['HOME']\n"


def _group_lines(ct, trees: dict, support: dict | None = None) -> list[str]:
    """What breaching a zero ceiling says about each group, past the two summary lines."""
    lines = ct.check_duplicate_helpers(trees, support or {})
    if not lines:
        return []
    assert [line.split(":")[0] for line in lines[:2]] == [
        "duplicated helper groups across test files",
        "duplicated helper copies across test files",
    ]
    return lines[2:]


class TestDuplicateHelpers:
    @pytest.fixture(autouse=True)
    def _zero_ceiling(self, ct, monkeypatch):
        monkeypatch.setattr(ct, "DUPLICATE_HELPER_CEILING", dict(_ZERO))

    def test_the_same_helper_body_in_several_files_is_one_group_naming_every_copy(self, ct):
        trees = _parsed({f"tests/test_{x}.py": _GIT for x in "cab"})
        assert _group_lines(ct, trees) == [
            f"_git (3 copies): tests/test_a.py:4, tests/test_b.py:4, tests/test_c.py:4 {_MOVE_IT}"
        ]

    # Rule (e)'s "the same body", as `helper_fingerprint` decides it: two files
    # each defining the last function of their source are one group or none.
    @pytest.mark.parametrize(
        ("first", "second", "same"),
        [
            pytest.param(
                _GIT,
                _GIT.replace("check=True, text=True", "text=True, check=True"),
                True,
                id="keyword-order",
            ),
            pytest.param(
                _GIT,
                _GIT.replace(
                    "def _git(*args):\n",
                    'def _git(*args: str) -> subprocess.CompletedProcess:\n    """Run git."""\n',
                ),
                True,
                id="docstring-and-annotations",
            ),
            pytest.param(
                _NESTED,
                'def f(x: int) -> int:\n    """Doc."""\n'
                "    def g(y: str):\n        return h(y, b=2, a=1)\n    return g(x)\n",
                True,
                id="annotations-and-keyword-order-in-a-nested-def",
            ),
            pytest.param(
                "DEFAULTS = {'a': 1}\n" + _ROW,
                "import os\nDEFAULTS = {'a': 1}\n" + _ROW,
                True,
                id="the-same-module-constant",
            ),
            pytest.param(
                "DEFAULTS = {'a': 1}\n" + _ROW,
                "DEFAULTS = {'a': 2}\n" + _ROW,
                False,
                id="another-module-constant",
            ),
            pytest.param(
                "from defaults import DEFAULTS\n" + _ROW, _ROW, True, id="an-imported-name"
            ),
            pytest.param(
                "import os\nos.environ['HOME'] = '/h'\n" + _ENV,
                _ENV,
                True,
                id="a-name-only-assigned-into",
            ),
        ],
    )
    def test_the_same_body_is_one_helper(self, ct, first, second, same):
        trees = _parsed({"tests/test_a.py": first, "tests/test_b.py": second})
        assert len(ct.duplicate_helper_counts(trees, {})) == (1 if same else 0)

    @pytest.mark.parametrize(
        ("source", "appended"),
        [
            ("DEFAULTS = {'a': 1}\n" + _ROW, "\nDEFAULTS = {'a': 1}"),
            ("DEFAULTS: dict = {'a': 1}\n" + _ROW, "\nDEFAULTS = {'a': 1}"),
            ("OTHER, DEFAULTS = 0, {'a': 1}\n" + _ROW, "\nDEFAULTS = (0, {'a': 1})"),
            ("OTHER = DEFAULTS = {'a': 1}\n" + _ROW, "\nDEFAULTS = {'a': 1}"),
            ("DEFAULTS: dict\nDEFAULTS = {'a': 1}\n" + _ROW, "\nDEFAULTS = {'a': 1}"),
            ("DEFAULTS = {}\nDEFAULTS = {'a': 1}\n" + _ROW, "\nDEFAULTS = {}\nDEFAULTS = {'a': 1}"),
            ("B = 2\nA = 1\n" + _ROW.replace("DEFAULTS", "B, **A"), "\nA = 1\nB = 2"),
        ],
        ids=[
            "plain",
            "annotated",
            "unpacked",
            "chained",
            "declared",
            "rebound",
            "sorted-by-name",
        ],
    )
    def test_fingerprint_appends_each_module_constant_the_body_names(self, ct, source, appended):
        module = ast.parse(source)
        body = ast.unparse(module.body[-1].body)
        assert ct.helper_fingerprint(module.body[-1], module) == body + appended

    _PUBLIC = "def commit_all(repo):\n    return run(repo, 'commit', '-am', 'files')\n"
    _PRIVATE = _PUBLIC.replace("commit_all", "_commit")

    # `tests/api/...` sorts before `tests/support/...`: a support function is still
    # the one every copy is told to use, underscore or not (`support.api._await_gate`
    # is a shared poller). A public name in a test file is no import target: with
    # no support function in the group, every copy is listed, in path order.
    @pytest.mark.parametrize(
        ("in_support", "in_test_b", "line"),
        [
            (
                _PUBLIC,
                _PRIVATE,
                "_commit (3 copies): tests/api/test_a.py:1, tests/test_b.py:1 "
                "-- use support.harness.commit_all (tests/support/harness.py:1)",
            ),
            (
                _PRIVATE,
                _PRIVATE,
                "_commit (3 copies): tests/api/test_a.py:1, tests/test_b.py:1 "
                "-- use support.harness._commit (tests/support/harness.py:1)",
            ),
            (
                None,
                _PUBLIC,
                f"_commit (2 copies): tests/api/test_a.py:1, tests/test_b.py:1 {_MOVE_IT}",
            ),
        ],
        ids=["public-support", "underscore-support", "public-name-in-a-test-file"],
    )
    def test_a_helper_matching_a_support_function_is_told_which_one_to_import(
        self, ct, in_support, in_test_b, line
    ):
        trees = _parsed({"tests/api/test_a.py": self._PRIVATE, "tests/test_b.py": in_test_b})
        support = _parsed({"tests/support/harness.py": in_support} if in_support else {})
        assert _group_lines(ct, trees, support) == [line]

    def test_a_genuinely_different_helper_with_the_same_name_is_not_flagged(self, ct):
        other = _GIT.replace("check=True", "check=False")
        trees = _parsed(
            {"tests/test_a.py": _GIT, "tests/test_b.py": _GIT, "tests/test_c.py": other},
        )
        (line,) = _group_lines(ct, trees)
        assert line.startswith("_git (2 copies): tests/test_a.py:4, tests/test_b.py:4 ")
        assert "test_c.py" not in line

    # `_commit` sits on line 8 of a file that is `_GIT` and then this.
    _COMMIT = "\n\ndef _commit(repo):\n    return run(repo, 'commit', '-am', 'files')\n"

    def _two_groups(self, ct) -> dict:
        """`_git` in three files and `_commit` in two: 2 groups, 5 copies."""
        trees = _parsed(
            {
                "tests/test_a.py": _GIT + self._COMMIT,
                "tests/test_b.py": _GIT + self._COMMIT,
                "tests/test_c.py": _GIT,
            },
        )
        assert sorted(ct.duplicate_helper_counts(trees, {}).values()) == [2, 3]
        return trees

    @pytest.mark.parametrize(("over", "actual"), [("groups", 2), ("copies", 5)])
    def test_copies_at_the_ceiling_pass_and_one_more_fails(self, ct, monkeypatch, over, actual):
        trees = self._two_groups(ct)
        monkeypatch.setattr(ct, "DUPLICATE_HELPER_CEILING", {"groups": 2, "copies": 5})
        assert ct.check_duplicate_helpers(trees, {}) == []
        monkeypatch.setitem(ct.DUPLICATE_HELPER_CEILING, over, actual - 1)
        summary, *groups = ct.check_duplicate_helpers(trees, {})
        assert summary == (
            f"duplicated helper {over} across test files: {actual}, over the ceiling of "
            f"{actual - 1} (dev/check_tests.py:DUPLICATE_HELPER_CEILING) -- it may shrink, not grow"
        )
        assert len(groups) == 2
        assert not any(line.startswith("duplicated helper ") for line in groups)

    def test_a_breach_names_every_group_with_its_copies(self, ct, monkeypatch):
        trees = self._two_groups(ct)
        support = _parsed(
            {"tests/support/harness.py": self._COMMIT.replace("_commit", "commit_all")}
        )
        # tests/support's public `commit_all` is a sixth copy, in the same two groups
        monkeypatch.setattr(ct, "DUPLICATE_HELPER_CEILING", {"groups": 2, "copies": 5})
        assert ct.check_duplicate_helpers(trees, support) == [
            "duplicated helper copies across test files: 6, over the ceiling of 5 "
            "(dev/check_tests.py:DUPLICATE_HELPER_CEILING) -- it may shrink, not grow",
            f"_git (3 copies): tests/test_a.py:4, tests/test_b.py:4, tests/test_c.py:4 {_MOVE_IT}",
            "_commit (3 copies): tests/test_a.py:8, tests/test_b.py:8 "
            "-- use support.harness.commit_all (tests/support/harness.py:3)",
        ]

    @pytest.mark.parametrize(("stale", "actual"), [("groups", 2), ("copies", 5)])
    def test_a_ceiling_more_than_its_margin_above_the_real_count_is_stale(
        self, ct, monkeypatch, stale, actual
    ):
        counts = {"_x#00000000": 3, "_y#00000000": 2}
        margin = ct.DUPLICATE_HELPER_STALE_MARGIN[stale]
        monkeypatch.setattr(ct, "DUPLICATE_HELPER_CEILING", {"groups": 2, "copies": 5})
        monkeypatch.setitem(ct.DUPLICATE_HELPER_CEILING, stale, actual + margin)
        assert ct.check_duplicate_helper_ceiling_is_current(counts) == []
        monkeypatch.setitem(ct.DUPLICATE_HELPER_CEILING, stale, actual + margin + 1)
        assert ct.check_duplicate_helper_ceiling_is_current(counts) == [
            f'DUPLICATE_HELPER_CEILING["{stale}"] = {actual + margin + 1} sits {margin + 1} '
            f"above the real {actual} -- tighten it to {actual} (margin is {margin})"
        ]

    def test_a_test_function_is_never_a_helper(self, ct):
        test = "\n\ndef test_x():\n    assert _git('status')\n"
        trees = _parsed({"tests/test_a.py": _GIT + test, "tests/test_b.py": _GIT + test})
        assert _group_lines(ct, trees) == [
            f"_git (2 copies): tests/test_a.py:4, tests/test_b.py:4 {_MOVE_IT}"
        ]

    def test_a_body_repeated_within_one_file_is_not_flagged(self, ct):
        again = _GIT.split("\n\n\n", 1)[1].replace("def _git", "def _git_again")
        trees = _parsed({"tests/test_a.py": _GIT + "\n\n" + again, "tests/test_b.py": "X = 1\n"})
        assert ct.duplicate_helper_counts(trees, {}) == {}
        assert ct.check_duplicate_helpers(trees, {}) == []

    def test_main_checks_both_testpaths_against_tests_support(
        self, ct, tmp_path, monkeypatch, capsys
    ):
        public = "def commit_all(repo):\n    return run(repo, 'commit', '-am', 'files')\n"
        copy = public.replace("commit_all", "_commit") + "\n\ndef test_c():\n    assert _commit\n"
        write(tmp_path, "tests/support/harness.py", public)
        write(tmp_path, "tests/test_a.py", copy)
        write(tmp_path, f"{ct.PLUGIN_TESTS.as_posix()}/test_p.py", copy)
        monkeypatch.setattr(ct, "ROOT", tmp_path)
        monkeypatch.setattr(ct, "LINE_BUDGET_ALLOWLIST", {})
        monkeypatch.setattr(ct, "EXPECTATION_ALLOWLIST", set())
        monkeypatch.setattr(ct, "REAL_CLI_ALLOWLIST", {})
        # one group over a ceiling of none, and a copies ceiling left far above its 3
        monkeypatch.setattr(ct, "DUPLICATE_HELPER_CEILING", {"groups": 0, "copies": 9})
        assert ct.main() == 1
        out = capsys.readouterr().out
        assert "duplicated helper groups across test files: 1, over the ceiling of 0" in out
        assert (
            "_commit (3 copies): plugins/kraft-lite/tests/test_p.py:1, tests/test_a.py:1 "
            "-- use support.harness.commit_all (tests/support/harness.py:1)"
        ) in out
        assert 'DUPLICATE_HELPER_CEILING["copies"] = 9 sits 6 above the real 3' in out
        assert "\n3 violation(s)" in out


class TestParseFailureIsAFailure:
    def test_an_unparsable_file_is_reported_not_skipped(self, ct, tmp_path):
        bad = write(tmp_path, "tests/test_broken.py", "def test_broken(:\n    pass\n")
        tree, error = ct._parse(bad, tmp_path)
        assert tree is None
        assert error is not None
        assert "could not parse" in error

    def test_main_exits_nonzero_when_any_file_fails_to_parse(self, ct, tmp_path, monkeypatch):
        write(tmp_path, "tests/test_broken.py", "def test_broken(:\n    pass\n")
        monkeypatch.setattr(ct, "ROOT", tmp_path)
        assert ct.main() == 1


class TestUnreadableFileIsAFailure:
    """A permission-denied file used to raise a raw `PermissionError` out of
    `_line_count` (called before `_parse` in `main()`'s loop) -- a crash with
    a traceback, not the intended violation line. `OSError` covers
    `PermissionError` and a file that vanishes between listing and reading
    alike."""

    @pytest.mark.skipif(os.getuid() == 0, reason="root ignores the mode bits")
    def test_line_count_returns_none_rather_than_raising(self, ct, tmp_path):
        path = tmp_path / "test_unreadable.py"
        path.write_text("def test_x():\n    assert True\n")
        path.chmod(0o000)
        try:
            assert ct._line_count(path) is None
        finally:
            path.chmod(0o644)

    @pytest.mark.skipif(os.getuid() == 0, reason="root ignores the mode bits")
    def test_check_line_budget_does_not_crash_on_an_unreadable_file(self, ct, tmp_path):
        path = tmp_path / "test_unreadable.py"
        path.write_text("def test_x():\n    assert True\n")
        path.chmod(0o000)
        try:
            assert ct.check_line_budget(path, "tests/test_unreadable.py") == []
        finally:
            path.chmod(0o644)

    @pytest.mark.skipif(os.getuid() == 0, reason="root ignores the mode bits")
    def test_parse_reports_it_as_a_violation_naming_the_file(self, ct, tmp_path):
        path = tmp_path / "test_unreadable.py"
        path.write_text("def test_x():\n    assert True\n")
        path.chmod(0o000)
        try:
            tree, error = ct._parse(path, tmp_path)
            assert tree is None
            assert error is not None
            assert "test_unreadable.py" in error
        finally:
            path.chmod(0o644)

    @pytest.mark.skipif(os.getuid() == 0, reason="root ignores the mode bits")
    def test_main_fails_rather_than_crashing_on_an_unreadable_file(self, ct, tmp_path, monkeypatch):
        path = write(tmp_path, "tests/test_unreadable.py", "def test_x():\n    assert True\n")
        path.chmod(0o000)
        monkeypatch.setattr(ct, "ROOT", tmp_path)
        try:
            assert ct.main() == 1
        finally:
            path.chmod(0o644)


class TestPluginTestsAreChecked:
    """`plugins/kraft-lite/tests` is the second testpath, held to the same
    rules as `tests/`."""

    def _tree_with(self, ct, tmp_path, monkeypatch, body: str) -> None:
        plugin = tmp_path / ct.PLUGIN_TESTS
        plugin.mkdir(parents=True)
        (plugin / "test_lite.py").write_text(body)
        (tmp_path / "tests").mkdir()
        monkeypatch.setattr(ct, "ROOT", tmp_path)
        monkeypatch.setattr(ct, "REAL_CLI_ALLOWLIST", {})
        monkeypatch.setattr(ct, "EXPECTATION_ALLOWLIST", set())
        monkeypatch.setattr(ct, "LINE_BUDGET_ALLOWLIST", {})
        monkeypatch.setattr(ct, "DUPLICATE_HELPER_CEILING", dict(_ZERO))

    def test_a_bad_plugin_test_fails_the_check(self, ct, tmp_path, monkeypatch, capsys):
        self._tree_with(ct, tmp_path, monkeypatch, "def test_x():\n    1 + 1\n")
        assert ct.main() == 1
        assert "plugins/kraft-lite/tests/test_lite.py:1: test_x has no assert" in (
            capsys.readouterr().out
        )

    def test_a_good_plugin_test_passes_it(self, ct, tmp_path, monkeypatch):
        self._tree_with(ct, tmp_path, monkeypatch, "def test_x():\n    assert 1 + 1 == 2\n")
        assert ct.main() == 0


class TestSelfCheck:
    def test_the_real_tree_passes_its_own_checker(self, ct):
        """The live-fire version of every case above: run the checker for
        real, against this repo's actual tests/ tree, exactly as CI does."""
        assert ct.main() == 0
