# Intent: targeted tests

What `just test-targeted` (`dev/targeted_tests.py`) runs for a change. testmon follows Python execution only, so a non-Python file a test reads has to be named here, either as blind (the full suite) or with the tests that read it.

## REQ kraft-lite-edit-runs-the-plugins-tests
WHEN a non-Python file under `plugins/kraft-lite` is edited, the targeted plan SHALL run the plugin's tests and the bundle parity test in place of the full suite.
enforced-by: tests/test_dev_targeted_tests.py::test_a_blind_change_with_known_readers_adds_them_instead_of_the_full_suite[kraft-lite-skill-edited], tests/test_dev_targeted_tests.py::test_a_blind_change_with_known_readers_adds_them_instead_of_the_full_suite[kraft-lite-method-edited], tests/test_dev_targeted_tests.py::test_a_blind_change_with_known_readers_adds_them_instead_of_the_full_suite[kraft-lite-eval-edited]
origin: dev/targeted_tests.py

## REQ kraft-lite-add-or-delete-runs-the-full-suite
IF a non-Python file under `plugins/kraft-lite` is added or deleted, THEN the targeted plan SHALL run the full suite.
enforced-by: tests/test_dev_targeted_tests.py::test_a_blind_change_with_known_readers_adds_them_instead_of_the_full_suite[kraft-lite-skill-added], tests/test_dev_targeted_tests.py::test_a_blind_change_with_known_readers_adds_them_instead_of_the_full_suite[kraft-lite-skill-deleted]
origin: dev/targeted_tests.py

## REQ kraft-lite-python-is-left-to-testmon
The targeted plan SHALL NOT treat a Python file under `plugins/kraft-lite` as invisible to testmon.
enforced-by: tests/test_dev_targeted_tests.py::test_the_plan_runs_the_full_suite_only_for_a_change_testmon_cannot_see[kraft-lite-python]
origin: dev/targeted_tests.py

## REQ kraft-skill-edit-runs-the-bundle-parity-test
WHEN a file under `src/kraft/skills` is edited, the targeted plan SHALL include the kraft-lite bundle parity test.
enforced-by: tests/test_dev_targeted_tests.py::test_a_blind_change_with_known_readers_adds_them_instead_of_the_full_suite[kraft-skill-edited]
origin: dev/targeted_tests.py

## REQ just-test-warns-about-kraft-lite-non-python
WHEN `just test` is run with a changed non-Python file under `plugins/kraft-lite`, the system SHALL list that file among the paths testmon cannot see.
enforced-by: tests/test_dev_targeted_tests.py::test_the_blind_command_lists_what_just_test_warns_about
origin: dev/targeted_tests.py

## REQ kraft-lite-evals-are-well-formed
The kraft-lite evals SHALL be a non-empty list of cases per installed skill, each with exactly `skills`, `query`, `setup` and `expected_behavior`, naming only installed `kraft-lite:` skills.
enforced-by: plugins/kraft-lite/tests/test_evals.py::test_every_skill_has_an_evals_file, plugins/kraft-lite/tests/test_evals.py::test_every_eval_case_is_well_formed[build-slice], plugins/kraft-lite/tests/test_evals.py::test_every_eval_case_is_well_formed[gate], plugins/kraft-lite/tests/test_evals.py::test_every_eval_case_is_well_formed[init], plugins/kraft-lite/tests/test_evals.py::test_every_eval_case_is_well_formed[next], plugins/kraft-lite/tests/test_evals.py::test_every_eval_case_is_well_formed[run-slices], plugins/kraft-lite/tests/test_evals.py::test_every_eval_case_is_well_formed[start], plugins/kraft-lite/tests/test_evals.py::test_every_eval_case_is_well_formed[status]
origin: dev/targeted_tests.py
