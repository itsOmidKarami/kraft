# What CI checks

Every pull request runs these jobs, except where the paths it changes decide
otherwise.

A docs-only pull request skips the jobs marked *(code)* and *(python)*, and
`docs tests` runs every unit test file that names a docs path in their place.
It is docs-only when every file it changes is a root-level `*.md`,
`docsite/**`, `.github/assets/**`, or `docs/*.md` (not `docs/intent/`). 

One
that changes those and `frontend/**`, and nothing else and no `.py` file,
skips the job marked *(python)* and runs the unit tier on Python 3.14 alone:
no Python moved, so every unit test still runs, once. A push to `main` runs
everything.

`just ci-test` runs `lint` and the unit tier, in CI's order, on
the full suite. `just test-targeted` is `just ci-test` with the tests testmon
picks, and the full suite when it can't see the change; Kraft's own
verification runs it.

| CI job | What it runs | Run it locally with |
|---|---|---|
| `lint` | ruff check and format, `dev/check_docs_coverage.py`, `dev/check_docs_walls.py`, `dev/check_docs_redirects.py`, `dev/check_docs_landings.py`, `dev/check_docs_shell.py`, `dev/check_docs_ledes.py`, `dev/check_docs_added_in.py`, `vale` (the docs' [voice and spelling](writing-docs.md#voice-and-spelling)), `dev/check_addon_versions.py`, `dev/check_tests.py` | `just ci-test`, or `just lint`, `just vale` and `just check-tests` |
| `changes` | decides whether the pull request is docs-only, or changes no Python, and so which Pythons the unit tier runs | nothing to run |
| `test (python 3.12 / 3.13 / 3.14 / 3.15, 1/4 … 4/4)` *(code)* | the unit tier (`-m "not e2e"`) in four shards, with `python -m kraft.intent` in the first; on 3.12, 3.13, 3.14 and 3.15 for a push to `main`; 3.12 and 3.14 for a pull request; 3.14 alone with no Python change | `just ci-test`, or `just test` and `just intent`; `just test-py 3.12` for another version |
| `test report` *(code)* | the shards' junit annotations and the coverage comment; reports only, so it isn't part of `test` | nothing to run |
| `docs tests` (docs-only pull requests) | the unit test files that name a docs path, on Python 3.14 | `just test` on the files the `git grep` in `test.yml`'s `docs-tests` job lists |
| `test` | the one check branch protection requires: it passes when every job above passes, and a job skipped because of the paths changed counts as passing (a docs-only pull request needs `docs tests` instead) | nothing to run |
| `e2e (real CLIs)` *(python)* | the e2e tier against real `bd`, bubblewrap, Docker and podman | `just test -m e2e --no-testmon`; a test whose CLI is missing skips |
| `kraft-lite on python 3.10 / 3.14` | `plugins/kraft-lite/tests` with nothing installed but pytest | `uv run --python 3.10 --no-project --with pytest pytest plugins/kraft-lite/tests -q` (or `3.14`) |
| `frontend` *(code)* | `npm ci`, `npm run build` (which typechecks), `npm test` | `just test-ui` |
| `vscode` *(code)* | typecheck, unit tests, integration tests | `just test-vscode`, then `npm run test:integration` in `vscode/` |
| `playwright` *(code)* | the browser e2e suite against a fixture server | `just e2e-ci` |
| `ui contract` *(code)* | the UI contract and icon-only audit: the built SPA in a browser on a mocked API, no Kraft server (`frontend/e2e/contract/`); then the dashboard demo's smoke suite (`frontend/e2e/demo/`) | `just ui-contract`, then `just demo-smoke` |
| `removals declared` | `dev/check_removals.py` against the PR description | [Removed tests](../CONTRIBUTING.md#removed-tests) |
| `release impact declared` | exactly one `release::*` label; it lives in `pr-labels.yml`, not `test.yml`, so labelling a pull request never starts or cancels the test run | see [Pull requests and release labels](../CONTRIBUTING.md#pull-requests-and-release-labels) |
| `docs` (only when `docsite/`, `.github/assets/`, `frontend/public/icon.svg` or the docs build scripts change) | `dev/build_docs_site.sh`: the latest release's pages, each older major's and `main`'s, all with this branch's site code; then `dev/check_llm_docs.py` (no root-relative links in `raw/*.md`, no landing-page anchors in `llms-full.txt`) and, on a PR, a link check | `just docs-site` |
| `docs nudge` | a comment when source moved without its docs page, or the API without the demo's mocks; never fails | nothing to run |
| `tests nudge` | the PR's test-tree delta as a comment (`dev/test_shape_report.py --diff`), only on one of the three signs its `worth_saying` states; never fails | `uv run python dev/test_shape_report.py --diff origin/main HEAD` |
| `codeql` | GitHub's static analysis | nothing to run |

Branch protection requires `test` alongside the kraft-lite checks, `removals declared` and `release impact declared`. Once the ruleset names only those, the supported Python range and the job list change without editing repo settings.
