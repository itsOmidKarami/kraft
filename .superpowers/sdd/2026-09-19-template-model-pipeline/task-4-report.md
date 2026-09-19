# Task 4 report: model-owned template materialization

## Result

Moved chain-definition projection onto `Template.materialize(...)`, added
`ChainNode.materialized_dict()` for the normalized node shape and fresh mutable
copies, and retained `materialize(...)` as a thin compatibility adapter.

## RED

Added `test_template_materialize_returns_the_existing_chain_definition_shape`
before production changes. The focused test failed for the intended missing
model method:

```text
$ just test tests/test_templates.py -k 'template_materialize_returns_the_existing_chain_definition_shape' --testmon-noselect
1 failed, 205 deselected
AttributeError: 'Template' object has no attribute 'materialize'
```

## GREEN

The required focused compatibility slice passed after the projection move:

```text
$ just test tests/test_templates.py tests/test_executor_entry.py tests/test_api_work_items.py -k 'materialize or attachment or skip_nodes' --testmon-noselect
29 passed, 234 deselected, 1 warning
```

The required backend compatibility slice and lint also passed:

```text
$ just test tests/test_templates.py tests/test_gates.py tests/test_api_gates.py tests/test_api_settings_templates.py tests/test_executor_entry.py tests/test_executor_walk.py tests/test_api_work_items.py --testmon-noselect && just lint
387 passed, 2 warnings
All checks passed!
319 files already formatted
```

## Files changed

- `src/kraft/templates.py`
- `tests/test_templates.py`
- `.superpowers/sdd/2026-09-19-template-model-pipeline/task-4-report.md`

## Self-review

- The projection preserves `{"template_id", "nodes"}`, gate trimming, skip-node
  filtering, normalized `tasks`/`steps`, and all existing node fields.
- `tasks`, nested `steps`, and `on_failure` are copied for each projection;
  the module-level wrapper delegates directly to the model method.
- Routes and executor signatures did not require changes because their existing
  compatibility calls remain valid.
- `git diff --check` passed.

## Concerns

The compatibility slice retains the existing Starlette `BlockingPortal`
deprecation warning and a concurrent-gates coroutine warning; neither is
introduced by this change.

## Commit

Recorded in the task commit after verification.
