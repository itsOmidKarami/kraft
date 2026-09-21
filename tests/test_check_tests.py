"""`dev/check_tests.py` is itself unexercised by the suite it checks -- these
pin its four rules against a deliberately bad file each, the two allowlist-
staleness rules that keep (c) and (d) from rotting into a one-way ratchet,
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

    def test_an_allowlisted_test_is_not_flagged_even_with_no_assert(self, ct):
        relpath, name = next(iter(ct.EXPECTATION_ALLOWLIST)).split("::")
        tree = _tree(f"def {name}():\n    1 + 1\n")
        assert ct.check_every_test_has_an_expectation(tree, relpath) == []


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


class TestSelfCheck:
    def test_the_real_tree_passes_its_own_checker(self, ct):
        """The live-fire version of every case above: run the checker for
        real, against this repo's actual tests/ tree, exactly as CI does."""
        assert ct.main() == 0
