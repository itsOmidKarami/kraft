# kraft-lite 3.10 floor job — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fail a merge request when `plugins/kraft-lite` stops working on Python 3.10, instead of finding out after publish.

**Architecture:** One new `lite-floor` job in `.gitlab-ci.yml`, gated by the same rules as the existing `lite-version` job. It runs the plugin's own suite under `uv run --python 3.10 --no-project`, which escapes the monorepo's `requires-python = ">=3.14"` and installs nothing but pytest.

**Tech Stack:** GitLab CI, `uv` (already the pipeline's base image, `ghcr.io/astral-sh/uv:python3.14-bookworm-slim`), pytest.

**Spec:** none — brainstormed as a bounded change; the design was approved in chat and recorded at chain `kl-44738d4a`'s `spec_approval` gate. `Kraft-brc` carries the problem statement.

## Global Constraints

- **The floor is 3.10**, the value `plugins/kraft-lite/.github/workflows/test.yml:12` claims and `plugins/kraft-lite/ruff.toml` targets. If those change, this job changes with them.
- **No dependency file for the plugin suite.** `--with pytest` and nothing else: the shipped helper is stdlib-only, and a dependency appearing here is the first sign that stopped being true. Same stance as the GitHub workflow.
- **MR-only, plugin changes only** — `if $CI_PIPELINE_SOURCE == "merge_request_event"` plus `changes: plugins/kraft-lite/**/*`, mirroring `lite-version` at `.gitlab-ci.yml:102`.
- **Do not commit or push without the user's say-so.** CLAUDE.md's conservative profile overrides this plan's commit step.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `.gitlab-ci.yml` | the `lite-floor` job | 1 |

No test file. The job *is* the test; Step 1 proves it has teeth by breaking the thing it exists to catch.

---

### Task 1: `lite-floor` job

Closes `Kraft-brc`.

**Files:**
- Modify: `.gitlab-ci.yml` — insert after the `lite-version` job ends at line 116, before `frontend:`

**Interfaces:**
- Consumes: the default image and `UV_CACHE_DIR` from `.gitlab-ci.yml:11-17`
- Produces: a CI job named `lite-floor`; nothing else in the repo references it

- [ ] **Step 1: Prove the check fails on the break it exists to catch**

The job would be green from birth, which proves nothing. Reintroduce the exact defect that motivated `Kraft-brc` — PEP 758 syntax, valid on 3.14 and not on 3.10:

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("plugins/kraft-lite/kl.py")
s = p.read_text()
assert s.count("    except (OSError, subprocess.SubprocessError):") == 1
p.write_text(s.replace("    except (OSError, subprocess.SubprocessError):",
                       "    except OSError, subprocess.SubprocessError:"))
PY
uv run --python 3.10 --no-project --with pytest pytest plugins/kraft-lite/tests -q
```

Expected: FAIL. Every test errors at collection with
`SyntaxError: except expressions without parentheses are only supported in Python 3.14 and greater`.

- [ ] **Step 2: Confirm 3.14 does not catch it**

```bash
uv run pytest plugins/kraft-lite/tests -q
```

Expected: the syntax itself is accepted by 3.14. `test_kl_parses_on_the_oldest_supported_python` fails — that guard was added for this defect — but the interpreter parses the file. This is the point: without a 3.10 run, only that one test stands between the break and a publish.

- [ ] **Step 3: Restore**

```bash
git checkout plugins/kraft-lite/kl.py
uv run pytest plugins/kraft-lite/tests -q
```

Expected: 127 passed.

- [ ] **Step 4: Add the job**

Insert into `.gitlab-ci.yml` after the `lite-version` job, before `frontend:`:

```yaml
# The plugin claims to need "python3 and nothing else", and its own workflow
# pins that floor at 3.10 — but that workflow only runs on the published repo,
# which is after a bad publish has already reached users. This is the same check
# one step earlier. `--no-project` is required: this repo's pyproject demands
# >=3.14, and uv refuses the older interpreter without it.
lite-floor:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
      changes:
        - plugins/kraft-lite/**/*
  script:
    - uv run --python 3.10 --no-project --with pytest pytest plugins/kraft-lite/tests -q
```

- [ ] **Step 5: Verify the job's command locally**

```bash
uv run --python 3.10 --no-project --with pytest pytest plugins/kraft-lite/tests -q
```

Expected: 127 passed.

- [ ] **Step 6: Verify the YAML parses**

```bash
python3 -c "
import sys
try:
    import yaml
except ImportError:
    sys.exit('pyyaml absent; rely on CI lint instead')
d = yaml.safe_load(open('.gitlab-ci.yml'))
print('lite-floor' in d, d['lite-floor']['rules'])
"
```

Expected: `True` and the rule showing the merge-request condition. If pyyaml is absent, the repo's `check yaml` pre-commit hook covers this at commit time.

- [ ] **Step 7: Commit**

```bash
git add .gitlab-ci.yml docs/superpowers/plans/2026-09-07-kraft-lite-floor-job.md
git commit -m "CI: run the kraft-lite suite on its 3.10 floor before merge (Kraft-brc)"
```

---

## Self-Review

**Spec coverage:** the design had four claims — MR-only trigger, `--python 3.10`, `--no-project`, `--with pytest` — and all four appear in the Step 4 YAML. The design's stated non-goal (catching a 3.14-only stdlib call in an unexercised path) needs no task.

**Placeholder scan:** none. Every step carries the literal command or YAML.

**Type consistency:** no code interfaces; the only name introduced is the job `lite-floor`, used consistently in Steps 4 and 6.

**Ordering:** Steps 1–3 must run before Step 4, otherwise the guard is never proven to fail. Step 3's `git checkout` is what makes Step 1 safe.
