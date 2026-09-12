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

# Everything a checkout-local instance needs to stay off the real ~/.kraft: its
# own home, and beads writing into the throwaway seed repo instead of this one.
dev_env := "KRAFT_HOME=" + justfile_directory() + "/.dev" + \
    " KRAFT_BD_CWD=" + justfile_directory() + "/.dev/repo" + \
    " KRAFT_FRONTEND_DIST=" + justfile_directory() + "/frontend/dist" + \
    " KRAFT_PORT=8766"

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

# Run backend only, against the dev home (127.0.0.1:8766)
api: _dev-home
    {{dev_env}} {{fake_agent}} uv run python -m kraft

# Run frontend dev server only (localhost:5173, proxies to backend)
ui:
    cd frontend && KRAFT_PORT=8766 npm run dev

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
    uv tool install --from . kraft --force
    @echo "installed. run: kraft"
    @grep -q "register-python-argcomplete kraft" ~/.zshrc 2>/dev/null || echo 'tip: add eval "$(register-python-argcomplete kraft)" to ~/.zshrc for tab completion'

# Backend tests, only those affected by your changes (pytest-testmon; data in
# .testmondata). `just test --no-testmon` runs everything; pass other args as
# usual, e.g. `just test -k search`. On the recipe, not in pyproject addopts:
# CI, the verify node and lite-floor call pytest directly and must stay full.
# COVERAGE_CORE=ctrace: coverage's default sysmon core on 3.14 can't record
# testmon's per-test contexts and silently under-selects. Drop it once
# coverage supports dynamic contexts under sysmon.
test *ARGS:
    COVERAGE_CORE=ctrace uv run pytest --testmon {{ARGS}}

# Check the intent tree: every enforced-by pin resolves, and list what nothing pins.
intent:
    uv run python -m kraft.intent

# Regenerate the Lite plugin's chain artifact from the YAML templates.
lite-build:
    uv run python dev/build_lite_chain.py

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

# Playwright e2e
e2e:
    cd frontend && npm run e2e

# `just e2e` assumes a human already started the fixture server (README in
# frontend/e2e/serve.py) -- not a contract a worker can meet. This builds the
# SPA, starts serve.py in the background, waits for it to print its base URL,
# points Playwright at it, and tears the server down on exit either way.
# Mirrors .gitlab-ci.yml's frontend-e2e job script; that job is the proof this
# sequence works, run on every frontend-touching MR.
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

# Lint + format check
lint:
    uv run ruff check .
    uv run ruff format --check .

# Autofix lint + format
fix:
    uv run ruff check --fix .
    uv run ruff format .

# What CI's blocking jobs run, in the same order, without --testmon: the
# recipe `verify` calls instead of `test` (Kraft-579). `just test` stays
# change-selected -- the right default for a human editing one file, and what
# CLAUDE.md tells contributors to use -- but a change-selected suite is a
# different question than "does this pass CI", and verify exists to answer
# the second one. No --testmon also means an empty selection cannot report
# success by accident: pytest's own exit code for "collected 0 items" is 5,
# which `adapters/subprocess.py`'s `_resolve` already reads as failed
# (Kraft-44t0) -- nothing here needs to special-case that, and nothing should.
#
# `tests/test_gitlab_ci_config.py::test_ci_test_recipe_covers_every_blocking_ci_script_line`
# fails if this drifts from `.gitlab-ci.yml`'s lint-and-test/slow-tests jobs --
# that test is where the second copy of this list lives; keep the two in step.
ci-test:
    uv run ruff check .
    uv run ruff format --check .
    uv run pytest -m "not e2e and not slow"
    uv run pytest -m "slow"
    uv run python -m kraft.intent

# Publish plugins/ as one marketplace holding both plugins, tagged per release.
#
# A real `git subtree split`, so the published history is the monorepo's own
# commits, not one regenerated commit: `plugins/` already has the layout the
# marketplace needs (`kraft/`, `kraft-lite/`, and `.claude-plugin/` at its root),
# which is the whole reason the kraft plugin lives there rather than inside the
# Python package. `just bundle` is what carries its skills into the wheel.
#
# Force-pushed, because a split is derived from this repo's history and rewriting
# that history changes every commit here. It replaces `lite-publish`, which
# published plugins/kraft-lite/ alone to its own repo. That repo keeps its last
# release and a README pointing here; it receives no more.
#
# The expiry is the same one lite-publish carried: the day the public repo has an
# external contributor,
# force-pushing destroys their merge base, so stop running this and make the
# public repo the source instead.
#
# The versions published are whatever is committed in each plugin.json. The
# auto-tag job writes them from the release tag before tagging, so a split
# publishes a truthful number rather than one stamped after the fact.
#
# The remote is called `plugins`, but the GitHub repo is `itsOmidKarami/kraft` -
# deliberately the same name the monorepo will take when it migrates off GitLab,
# so `/plugin marketplace add itsOmidKarami/kraft` is correct now and stays
# correct afterwards. Until then GitHub `kraft` holds only the split plugins and
# GitLab `kraft` holds the source; they share a name and nothing else.
#
# Prerequisite: git remote add plugins git@github.com:itsOmidKarami/kraft.git
plugins-publish:
    #!/usr/bin/env bash
    set -euo pipefail
    # A tag must exist: the published plugin.json versions are written from it by
    # the auto-tag job, so publishing before the first release would ship 0.0.0.
    version=$(git describe --tags --abbrev=0 2>/dev/null | sed 's/^v//') || true
    if [ -z "${version:-}" ]; then echo "no release tag yet -- nothing to publish"; exit 1; fi
    just lite-build
    git diff --exit-code plugins/kraft-lite/chains/default.json
    uv run pytest tests/test_init.py plugins/kraft-lite/tests -q
    test -f plugins/kraft/LICENSE
    test -f plugins/kraft-lite/LICENSE
    claude plugin validate plugins --strict
    tag="v$version"
    # Same exit-code reading as lite-publish: 0 means this version is already
    # out, 2 means it is not, and anything else is a remote that could not be
    # reached -- which must not read as a clean publish.
    rc=0; git ls-remote --exit-code --tags plugins "$tag" >/dev/null || rc=$?
    case $rc in
        0) echo "kraft plugins $tag is already published -- tag a new release first"; exit 1 ;;
        2) ;;
        *) echo "cannot reach the plugins remote: git ls-remote exited $rc"; exit 1 ;;
    esac
    # No local tag or branch is created. The published tag has the same name as
    # this repo's own release tag, so `git tag "$tag"` here would collide with it
    # -- and the `git tag -d` that lite-publish uses to clear the way would
    # delete the real release tag. Pushing the split sha straight to the remote's
    # refs avoids touching this repo's ref namespace at all.
    split=$(git subtree split --prefix=plugins)
    # Atomic: a tag push that fails after main moved would leave the release
    # untagged, and the next force-push makes that commit unreachable.
    git push --force --atomic plugins "$split:refs/heads/main" "$split:refs/tags/$tag"
    echo "published kraft plugins $tag"
