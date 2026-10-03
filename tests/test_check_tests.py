"""`dev/check_tests.py` is itself unexercised by the suite it checks -- these
pin its five rules against a deliberately bad file each, the allowlist-
staleness rules that keep (c), (d) and (e) from rotting into a one-way ratchet,
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


def _parsed(root: Path, files: dict[str, str]) -> dict[str, ast.Module]:
    """Write each `relpath: source` under `root` and parse it back, keyed by relpath."""
    trees = {}
    for relpath, source in files.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        trees[relpath] = ast.parse(path.read_text())
    return trees


# `_git` sits on line 4 of every file built from this.
_GIT = (
    "import subprocess\n\n\n"
    "def _git(*args):\n"
    '    return subprocess.run(["git", *args], check=True, text=True)\n'
)


class TestDuplicateHelpers:
    @pytest.fixture(autouse=True)
    def _empty_allowlist(self, ct, monkeypatch):
        monkeypatch.setattr(ct, "DUPLICATE_HELPER_ALLOWLIST", {})

    def test_the_same_helper_body_in_two_files_is_flagged_once_per_extra_copy(self, ct, tmp_path):
        trees = _parsed(tmp_path, {f"tests/test_{x}.py": _GIT for x in "cab"})
        assert ct.check_duplicate_helpers(trees, {}) == [
            f"tests/test_{x}.py:4: _git() has the same body as _git() at tests/test_a.py:4 "
            f"-- move it into tests/support/ and import it from there"
            for x in "bc"
        ]

    def test_a_body_that_differs_only_in_keyword_order_is_the_same_body(self, ct, tmp_path):
        reordered = _GIT.replace("check=True, text=True", "text=True, check=True")
        trees = _parsed(tmp_path, {"tests/test_a.py": _GIT, "tests/test_b.py": reordered})
        (violation,) = ct.check_duplicate_helpers(trees, {})
        assert violation.startswith("tests/test_b.py:4: _git() has the same body as _git() at ")

    def test_a_body_that_differs_only_in_a_docstring_or_annotation_is_the_same_body(
        self, ct, tmp_path
    ):
        dressed = _GIT.replace(
            "def _git(*args):\n",
            'def _git(*args: str) -> subprocess.CompletedProcess:\n    """Run git."""\n',
        )
        trees = _parsed(tmp_path, {"tests/test_a.py": _GIT, "tests/test_b.py": dressed})
        (violation,) = ct.check_duplicate_helpers(trees, {})
        assert violation.startswith("tests/test_b.py:4: _git() has the same body as _git() at ")

    def test_a_body_over_a_different_module_constant_is_a_different_helper(self, ct, tmp_path):
        row = "def _row(**f):\n    return {**DEFAULTS, **f}\n"
        trees = _parsed(
            tmp_path,
            {
                "tests/test_a.py": "DEFAULTS = {'status': 'paused'}\n\n\n" + row,
                "tests/test_b.py": "DEFAULTS = {'status': 'done'}\n\n\n" + row,
            },
        )
        assert ct.check_duplicate_helpers(trees, {}) == []
        trees |= _parsed(tmp_path, {"tests/test_c.py": "DEFAULTS = {'status': 'done'}\n\n\n" + row})
        (violation,) = ct.check_duplicate_helpers(trees, {})
        assert violation.startswith("tests/test_c.py:4: _row() has the same body as _row() at ")
        assert "tests/test_b.py:4" in violation

    def test_a_helper_matching_a_support_function_is_told_which_one_to_import(self, ct, tmp_path):
        public = "def commit_all(repo):\n    return run(repo, 'commit', '-am', 'files')\n"
        copy = public.replace("commit_all", "_commit")
        # `tests/api/...` sorts before `tests/support/...`: the support function is
        # still the one every copy is told to use.
        trees = _parsed(tmp_path, {"tests/api/test_a.py": copy, "tests/test_b.py": copy})
        support = _parsed(tmp_path, {"tests/support/harness.py": public})
        assert ct.check_duplicate_helpers(trees, support) == [
            f"{relpath}:1: _commit() has the same body as support.harness.commit_all "
            f"(tests/support/harness.py:1) -- use support.harness.commit_all instead"
            for relpath in ("tests/api/test_a.py", "tests/test_b.py")
        ]
        # a private support function is no import target, and nor is a public
        # name in a test file: the first copy in path order is the original then
        trees = _parsed(tmp_path, {"tests/api/test_a.py": copy, "tests/test_b.py": public})
        private = _parsed(tmp_path, {"tests/support/harness.py": copy})
        assert ct.check_duplicate_helpers(trees, private) == [
            f"{relpath}:1: {name}() has the same body as _commit() at tests/api/test_a.py:1 "
            f"-- move it into tests/support/ and import it from there"
            for relpath, name in (
                ("tests/support/harness.py", "_commit"),
                ("tests/test_b.py", "commit_all"),
            )
        ]

    def test_a_genuinely_different_helper_with_the_same_name_is_not_flagged(self, ct, tmp_path):
        other = _GIT.replace("check=True", "check=False")
        trees = _parsed(
            tmp_path,
            {"tests/test_a.py": _GIT, "tests/test_b.py": _GIT, "tests/test_c.py": other},
        )
        (violation,) = ct.check_duplicate_helpers(trees, {})
        assert violation.startswith("tests/test_b.py:4:")
        assert "test_c.py" not in violation

    def test_an_allowlisted_name_may_not_gain_a_copy(self, ct, tmp_path, monkeypatch):
        # a renamed copy still counts against the entry, keyed by the commonest name
        renamed = _GIT.replace("def _git", "def _call_git")
        trees = _parsed(
            tmp_path,
            {"tests/test_a.py": _GIT, "tests/test_b.py": _GIT, "tests/test_c.py": renamed},
        )
        (key,) = ct.duplicate_helper_counts(trees, {})
        assert key.startswith("_git#")
        monkeypatch.setattr(ct, "DUPLICATE_HELPER_ALLOWLIST", {key: 3})
        assert ct.check_duplicate_helpers(trees, {}) == []
        monkeypatch.setattr(ct, "DUPLICATE_HELPER_ALLOWLIST", {key: 2})
        assert ct.check_duplicate_helpers(trees, {}) == [
            f"{key}: 3 copies, over its allowlisted ceiling of 2 "
            f"(dev/check_tests.py:DUPLICATE_HELPER_ALLOWLIST) -- it may shrink, not grow: "
            f"move the copy at tests/test_a.py:4 into tests/support/ and import it "
            f"instead of adding a copy"
        ]
        # once tests/support has it as a public function, that is the one named
        support = _parsed(tmp_path, {"tests/support/harness.py": _GIT.replace("_git", "git")})
        assert ct.duplicate_helper_counts(trees, support) == {key: 4}
        monkeypatch.setattr(ct, "DUPLICATE_HELPER_ALLOWLIST", {key: 3})
        (violation,) = ct.check_duplicate_helpers(trees, support)
        assert violation.endswith(
            "use support.harness.git (tests/support/harness.py:4) instead of adding a copy"
        )

    def test_an_allowlisted_name_whose_count_fell_to_one_is_stale(self, ct, tmp_path, monkeypatch):
        trees = _parsed(tmp_path, {"tests/test_a.py": _GIT, "tests/test_b.py": _GIT})
        counts = ct.duplicate_helper_counts(trees, {})
        (key,) = counts
        monkeypatch.setattr(ct, "DUPLICATE_HELPER_ALLOWLIST", {key: 2})
        assert ct.check_duplicate_helper_allowlist_is_current(counts) == []
        del trees["tests/test_b.py"]
        assert ct.check_duplicate_helper_allowlist_is_current(
            ct.duplicate_helper_counts(trees, {})
        ) == [
            f"DUPLICATE_HELPER_ALLOWLIST[{key!r}] = 2 is stale: it is no longer duplicated "
            f"across test files -- remove the entry"
        ]

    def test_an_allowlisted_ceiling_too_far_above_the_real_count_is_stale(self, ct, monkeypatch):
        ceiling = 3 + ct.STALE_ALLOWLIST_MARGIN_COPIES
        monkeypatch.setattr(ct, "DUPLICATE_HELPER_ALLOWLIST", {"_x#00000000": ceiling})
        assert ct.check_duplicate_helper_allowlist_is_current({"_x#00000000": 3}) == []
        monkeypatch.setattr(ct, "DUPLICATE_HELPER_ALLOWLIST", {"_x#00000000": ceiling + 1})
        (violation,) = ct.check_duplicate_helper_allowlist_is_current({"_x#00000000": 3})
        assert "tighten it to 3" in violation

    def test_a_test_function_is_never_a_helper(self, ct, tmp_path):
        test = "\n\ndef test_x():\n    assert _git('status')\n"
        trees = _parsed(tmp_path, {"tests/test_a.py": _GIT + test, "tests/test_b.py": _GIT + test})
        (violation,) = ct.check_duplicate_helpers(trees, {})
        assert violation.startswith("tests/test_b.py:4: _git() ")

    def test_a_body_repeated_within_one_file_is_not_flagged(self, ct, tmp_path):
        again = _GIT.split("\n\n\n", 1)[1].replace("def _git", "def _git_again")
        trees = _parsed(
            tmp_path, {"tests/test_a.py": _GIT + "\n\n" + again, "tests/test_b.py": "X = 1\n"}
        )
        assert ct.duplicate_helper_counts(trees, {}) == {}
        assert ct.check_duplicate_helpers(trees, {}) == []

    def test_main_checks_both_testpaths_against_tests_support(
        self, ct, tmp_path, monkeypatch, capsys
    ):
        public = "def commit_all(repo):\n    return run(repo, 'commit', '-am', 'files')\n"
        copy = public.replace("commit_all", "_commit") + "\n\ndef test_c():\n    assert _commit\n"
        _parsed(
            tmp_path,
            {
                "tests/support/harness.py": public,
                "tests/test_a.py": copy,
                f"{ct.PLUGIN_TESTS.as_posix()}/test_p.py": copy,
            },
        )
        monkeypatch.setattr(ct, "ROOT", tmp_path)
        monkeypatch.setattr(ct, "TESTS", tmp_path / "tests")
        monkeypatch.setattr(ct, "LINE_BUDGET_ALLOWLIST", {})
        monkeypatch.setattr(ct, "EXPECTATION_ALLOWLIST", set())
        monkeypatch.setattr(ct, "REAL_CLI_ALLOWLIST", {})
        monkeypatch.setattr(ct, "DUPLICATE_HELPER_ALLOWLIST", {"_gone#00000000": 2})
        assert ct.main() == 1
        out = capsys.readouterr().out
        for relpath in ("plugins/kraft-lite/tests/test_p.py", "tests/test_a.py"):
            assert f"{relpath}:1: _commit() has the same body as support.harness.commit_all" in out
        assert "DUPLICATE_HELPER_ALLOWLIST['_gone#00000000'] = 2 is stale" in out
        assert "\n3 violation(s)" in out


class TestParseFailureIsAFailure:
    def test_an_unparsable_file_is_reported_not_skipped(self, ct, tmp_path, monkeypatch):
        monkeypatch.setattr(ct, "ROOT", tmp_path)
        bad = tmp_path / "test_broken.py"
        bad.write_text("def test_broken(:\n    pass\n")
        tree, error = ct._parse(bad)
        assert tree is None
        assert error is not None
        assert "could not parse" in error

    def test_main_exits_nonzero_when_any_file_fails_to_parse(self, ct, tmp_path, monkeypatch):
        (tmp_path / "test_broken.py").write_text("def test_broken(:\n    pass\n")
        monkeypatch.setattr(ct, "ROOT", tmp_path)
        monkeypatch.setattr(ct, "TESTS", tmp_path)
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
    def test_parse_reports_it_as_a_violation_naming_the_file(self, ct, tmp_path, monkeypatch):
        monkeypatch.setattr(ct, "ROOT", tmp_path)
        path = tmp_path / "test_unreadable.py"
        path.write_text("def test_x():\n    assert True\n")
        path.chmod(0o000)
        try:
            tree, error = ct._parse(path)
            assert tree is None
            assert error is not None
            assert "test_unreadable.py" in error
        finally:
            path.chmod(0o644)

    @pytest.mark.skipif(os.getuid() == 0, reason="root ignores the mode bits")
    def test_main_fails_rather_than_crashing_on_an_unreadable_file(self, ct, tmp_path, monkeypatch):
        path = tmp_path / "test_unreadable.py"
        path.write_text("def test_x():\n    assert True\n")
        path.chmod(0o000)
        monkeypatch.setattr(ct, "ROOT", tmp_path)
        monkeypatch.setattr(ct, "TESTS", tmp_path)
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
        monkeypatch.setattr(ct, "TESTS", tmp_path / "tests")
        monkeypatch.setattr(ct, "REAL_CLI_ALLOWLIST", {})
        monkeypatch.setattr(ct, "EXPECTATION_ALLOWLIST", set())
        monkeypatch.setattr(ct, "LINE_BUDGET_ALLOWLIST", {})
        monkeypatch.setattr(ct, "DUPLICATE_HELPER_ALLOWLIST", {})

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
