# Kraft dev tasks. Run `just` to list.

set shell := ["bash", "-uc"]

_default:
    @just --list

# Install backend + frontend deps
setup:
    uv sync
    cd frontend && npm install

# Install deps including semantic search (downloads a ~130MB model on first
# search). Without this, /search still works in fts mode.
setup-vector:
    uv sync --extra vector
    cd frontend && npm install

# The dev instance's port, in one place (Kraft-y0g2). It used to be written out
# three times -- here, in `ui`, and as `vite.config.ts`'s fallback -- and the
# three disagreed: vite said 8765, which is an installed daemon's default, so a
# bare `npm run dev` proxied to the operator's real instance.
#
# 8766 is a default, not a guarantee. `python -m kraft` refuses to start when
# something already answers on its host:port (Kraft-kquf), so a collision here
# is loud rather than a silent second server on somebody else's address -- but
# an operator whose daemon sits on 8766 still needs a way out, hence the
# override. Set KRAFT_DEV_PORT and both halves of `just dev` follow it.
dev_port := env_var_or_default("KRAFT_DEV_PORT", "8766")

# Everything a checkout-local instance needs to stay off the real ~/.kraft: its
# own home, and beads writing into the throwaway seed repo instead of this one.
dev_env := "KRAFT_HOME=" + justfile_directory() + "/.dev" + \
    " KRAFT_BD_CWD=" + justfile_directory() + "/.dev/repo" + \
    " KRAFT_FRONTEND_DIST=" + justfile_directory() + "/frontend/dist" + \
    " KRAFT_PORT=" + dev_port

# The fake agent ahead of the real `claude`, so a dev instance never spends tokens.
fake_agent := "PATH=" + justfile_directory() + "/fixtures/bin:$PATH"

# A dev home starts as a copy of the tracked templates/, plus the throwaway repo
# beads and the seeded work items live in. Copied, not pointed at: the Settings
# screens write to this directory, and dev edits must not land in the repo's real
# config. access.yaml never comes along — it is this machine's bind address and
# password hash, and a dev instance must stay on unauthenticated loopback.
_dev-home:
    @mkdir -p .dev
    @[ -d .dev/templates ] || cp -R templates .dev/templates
    @rm -f .dev/templates/access.yaml
    @{{dev_env}} uv run python dev/seed.py --repo-only

# Run backend only, against the dev home (127.0.0.1:8766, or KRAFT_DEV_PORT)
api: _dev-home
    {{dev_env}} {{fake_agent}} uv run python -m kraft

# Run frontend dev server only (localhost:5173, proxies to backend)
ui:
    cd frontend && KRAFT_PORT={{dev_port}} npm run dev

# Dev instance: backend + vite, fake agents, state in .dev/ (Ctrl-C stops both)
dev: _dev-home
    #!/usr/bin/env bash
    set -uo pipefail
    trap 'kill 0' EXIT
    export {{dev_env}}
    {{fake_agent}} uv run python -m kraft &
    cd frontend && npm run dev &
    wait

# Fill a running dev instance with work items in every interesting state
dev-seed: _dev-home
    {{dev_env}} uv run python dev/seed.py

# Throw the dev instance away (DB, logs, worktrees, seed repo, config)
dev-reset:
    rm -rf .dev

# ponytail: only a `just bundle` build produces a correct wheel -- a bare
# `uv build` still matches the `_bundled/**/*` glob against nothing. The two
# guards are `admin doctor`'s spa bundle check and the release job's smoke
# install; a `build_py` subclass is the upgrade if a wheel is ever built by
# something that calls neither.
#
# Build the SPA and default config into the package tree. `install` and the
# release pipeline both call this, so the two cannot drift.
bundle:
    cd frontend && npm run build
    rm -rf src/kraft/_bundled
    mkdir -p src/kraft/_bundled
    cp -R frontend/dist src/kraft/_bundled/web
    cp -R templates src/kraft/_bundled/templates
    # The agent skills live in plugins/kraft/ so they can be published beside
    # kraft-lite in one marketplace. An installed Kraft has no plugins/ beside
    # it, so they ride into the wheel here with the SPA.
    cp -R plugins/kraft/skills src/kraft/_bundled/plugin-skills
    # never ship a local access.yaml or notify.yaml: one holds this machine's
    # password hash, the other a webhook URL that usually embeds a bearer
    # token. `cp -R` does not know either is secret -- `.gitignore` only keeps
    # them out of the commit, not out of the wheel or the homes it seeds.
    rm -f src/kraft/_bundled/templates/access.yaml
    rm -f src/kraft/_bundled/templates/notify.yaml

# Install `kraft` as a real command (then just run `kraft` from anywhere).
# State lands in ~/.kraft, seeded from templates/ on first run.
install: bundle
    uv tool install --from . kraft-sdlc --force
    @echo "installed. run: kraft"
    @grep -q "register-python-argcomplete kraft" ~/.zshrc 2>/dev/null || echo 'tip: add eval "$(register-python-argcomplete kraft)" to ~/.zshrc for tab completion'

# Backend tests, only those affected by your changes (pytest-testmon; data in
# .testmondata). `just test --no-testmon` runs everything; pass other args as
# usual, e.g. `just test -k search`. On the recipe, not in pyproject addopts:
# CI, the verify node and lite-floor call pytest directly and must stay full.
# COVERAGE_CORE=ctrace: coverage's default sysmon core on 3.14 can't record
# testmon's per-test contexts and silently under-selects. Drop it once
# coverage supports dynamic contexts under sysmon.
# [positional-arguments] + "$@": `{{ARGS}}` interpolates a variadic parameter as
# plain recipe text joined by spaces, so the shell re-splits it and
# `just test -k "a or b"` reached pytest as three words (Kraft-s7c04.37). The
# attribute is per-recipe on purpose -- file-level `set positional-arguments`
# would change $0/$@ for every recipe here.
#
# Kraft-1v6ow: with --testmon active, pytest's own "collected 0 items" exit
# code (5, a failure) never reaches us -- testmon overrides it to 0 even when
# nothing was collected at all, not merely deselected by testmon itself (that
# case reads "collected N items / N deselected / 0 selected", never "collected
# 0 items"). A path argument that matches nothing must not look like a run
# that found nothing wrong, so we grep the one line pytest emits only for a
# truly empty collection and fail on it ourselves, when args were given.
[positional-arguments]
test *ARGS:
    #!/usr/bin/env bash
    set -uo pipefail
    log=$(mktemp -t kraft-test.XXXXXX)
    trap 'rm -f "$log"' EXIT
    COVERAGE_CORE=ctrace uv run pytest --testmon "$@" 2>&1 | tee "$log"
    status=${PIPESTATUS[0]}
    if [ -n "$*" ] && grep -qE '^collected 0 items$' "$log"; then
        echo "error: just test collected 0 items for the given args -- treating as a failure (Kraft-1v6ow)" >&2
        exit 1
    fi
    exit "$status"

# Check the intent tree: every enforced-by pin resolves, and list what nothing pins.
intent:
    uv run python -m kraft.intent

# Check the test suite against docs/testing.md's mechanical rules: e2e markers
# name a CLI, no unit test reaches a real bd/claude/gh/glab, the per-file line
# budget, every test has an expectation.
check-tests:
    uv run python dev/check_tests.py

# Frontend typecheck + unit tests. `npm test` is vitest, which does NOT typecheck;
# CI's `npm run build` runs `tsc -b` and will fail on errors vitest sails past. Keep
# the two in step here, or the only way to find a type error is to spend a pipeline.
#
# node_modules is gitignored, so a fresh worktree (every Kraft worker gets one)
# has none, and `npx tsc` would silently fetch an unrelated `tsc` package from
# the registry instead of failing. Install first; the cost is only on the first
# run in a worktree.
test-ui:
    cd frontend && [ -d node_modules ] || npm ci
    cd frontend && npx tsc -b
    cd frontend && npm test

# Kraft-m2wru: launch every (harness, model, effort) the shipped library uses
# once against the real CLI. Spends a few cents. Tests run with a temp HOME, so
# a `claude login` is invisible to them: export ANTHROPIC_API_KEY or
# CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`) first. The pre-commit hook
# runs this when a commit touches templates/ or the bundled harnesses.
smoke-models:
    @[ -n "${ANTHROPIC_API_KEY:-}${CLAUDE_CODE_OAUTH_TOKEN:-}" ] || { echo "smoke-models: export ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN (claude setup-token) -- tests use a temp HOME, so claude login is not seen" >&2; exit 1; }
    KRAFT_E2E=1 KRAFT_E2E_REQUIRE=claude just test tests/test_shipped_models.py -k real_cli --no-testmon

# The pre-commit hook's door to smoke-models: a Kraft worker or a shell with no
# credential skips it loudly instead of blocking the commit. smoke-models stays strict.
smoke-models-hook:
    #!/usr/bin/env bash
    if [ -n "${KRAFT_WORK_ITEM_ID:-}" ]; then
        echo "SKIPPED: smoke-models (shipped models on the real CLI): this is a Kraft worker (KRAFT_WORK_ITEM_ID set); run 'just smoke-models' before release" >&2
        exit 0
    fi
    if [ -z "${ANTHROPIC_API_KEY:-}${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
        echo "SKIPPED: smoke-models (shipped models on the real CLI): neither ANTHROPIC_API_KEY nor CLAUDE_CODE_OAUTH_TOKEN is set; run 'just smoke-models' with one before release" >&2
        exit 0
    fi
    exec just smoke-models

# Playwright e2e
e2e:
    cd frontend && npm run e2e

# `just e2e` assumes a human already started the fixture server (see
# frontend/e2e/README.md) -- not a contract a worker can meet. This builds the
# SPA, starts serve.py in the background, waits for it to print its base URL,
# points Playwright at it, and tears the server down on exit either way.
# Mirrors the `playwright` job in .github/workflows/test.yml; that job is the
# proof this sequence works, run on every frontend-touching PR.
e2e-ci:
    #!/usr/bin/env bash
    set -euo pipefail
    cd frontend
    npm ci --no-audit --no-fund && npx playwright install --with-deps chromium
    npm run build
    cd ..
    LOG=$(mktemp -t kraft-e2e-serve.XXXXXX)
    uv run python frontend/e2e/serve.py > "$LOG" 2>&1 &
    SERVE_PID=$!
    trap 'kill "$SERVE_PID" 2>/dev/null; wait "$SERVE_PID" 2>/dev/null; rm -f "$LOG"' EXIT
    for i in $(seq 1 60); do
        grep -q KRAFT_E2E_BASE "$LOG" && break
        kill -0 "$SERVE_PID" 2>/dev/null || { cat "$LOG"; exit 1; }
        sleep 1
    done
    export KRAFT_E2E_REPO=$(sed -n 's/.*KRAFT_E2E_REPO=//p' "$LOG")
    export KRAFT_E2E_BASE=$(sed -n 's/.*KRAFT_E2E_BASE=//p' "$LOG")
    test -n "${KRAFT_E2E_REPO:-}" || { cat "$LOG"; exit 1; }
    test -n "${KRAFT_E2E_BASE:-}" || { cat "$LOG"; exit 1; }
    cd frontend && npm run e2e -- --max-failures=3

# Regenerate the config JSON Schemas under vscode/schemas/
schemas:
    uv run python dev/export_config_schemas.py

# Lint + format check
lint:
    uv run ruff check .
    uv run ruff format --check .

# Autofix lint + format
fix:
    uv run ruff check --fix .
    uv run ruff format .

# What CI's blocking jobs run, in the same order, without --testmon: the
# verify node calls this instead of `test` (Kraft-579). `just test` stays
# change-selected -- the right default for a human editing one file, and what
# CLAUDE.md tells contributors to use -- but a change-selected suite is a
# different question than "does this pass CI", and this recipe exists to
# answer the second one. No --testmon also means an empty selection cannot
# report success by accident: pytest's own exit code for "collected 0 items"
# is 5, which `adapters/subprocess.py`'s `_resolve` already reads as failed
# (Kraft-44t0) -- nothing here needs to special-case that, and nothing should.
#
# Keep this in step with .github/workflows/test.yml's `test` job by hand --
# there's no test enforcing it since GitLab CI's config (and the test that
# checked it) was retired.
ci-test:
    uv run ruff check .
    uv run ruff format --check .
    uv run pytest -m "not e2e" -n auto
    uv run python -m kraft.intent
