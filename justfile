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
    " KRAFT_FRONTEND_DIST=" + justfile_directory() + "/frontend/dist"

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

# Run backend only, against the dev home (127.0.0.1:8765)
api: _dev-home
    {{dev_env}} {{fake_agent}} uv run python -m kraft

# Run frontend dev server only (localhost:5173, proxies to backend)
ui:
    cd frontend && npm run dev

# Dev instance: backend + vite, fake agents, state in .dev/ (Ctrl-C stops both)
dev: _dev-home
    #!/usr/bin/env bash
    set -uo pipefail
    trap 'kill 0' EXIT
    {{dev_env}} {{fake_agent}} uv run python -m kraft &
    cd frontend && npm run dev &
    wait

# Fill a running dev instance with work items in every interesting state
dev-seed: _dev-home
    {{dev_env}} uv run python dev/seed.py

# Throw the dev instance away (DB, logs, worktrees, seed repo, config)
dev-reset:
    rm -rf .dev

# ponytail: the bundle is copied here rather than by a setuptools build hook, so a
# wheel built any other way ships no SPA (the API degrades to JSON-only, visibly).
# Upgrade to a build_py subclass the day a wheel is built anywhere but this laptop.
# Install `kraft` as a real command (then just run `kraft` from anywhere).
# State lands in ~/.kraft, seeded from templates/ on first run.
install:
    cd frontend && npm run build
    rm -rf src/kraft/_bundled
    mkdir -p src/kraft/_bundled
    cp -R frontend/dist src/kraft/_bundled/web
    cp -R templates src/kraft/_bundled/templates
    # never ship a local access.yaml or notify.yaml: one holds this machine's
    # password hash, the other a webhook URL that usually embeds a bearer
    # token. `cp -R` does not know either is secret -- `.gitignore` only keeps
    # them out of the commit, not out of the wheel or the homes it seeds.
    rm -f src/kraft/_bundled/templates/access.yaml
    rm -f src/kraft/_bundled/templates/notify.yaml
    uv tool install --from . kraft --force
    @echo "installed. run: kraft"

# Backend tests (add args, e.g. `just test -k search`)
test *ARGS:
    uv run pytest {{ARGS}}

# Regenerate the Lite plugin's chain artifact from the YAML templates.
lite-build:
    uv run python dev/build_lite_chain.py

# The published history is REGENERATED each time and force-pushed. That is
# deliberate: this repo's own release will rewrite its history, which changes
# every commit a split derives from, and a preserved history would stop
# fast-forwarding the moment that lands.
#
# It is also why this recipe has an expiry. The day the public repo has an
# external contributor, force-pushing destroys their merge base: stop running
# this, and make the public repo the source instead.
#
# One-way. An outside PR comes back by cherry-pick into this repo, never by
# pulling into the public one.
#
# Publish plugins/kraft-lite/ to its own public repo; regenerates and force-pushes.
lite-publish:
    #!/usr/bin/env bash
    set -euo pipefail
    just lite-build
    git diff --exit-code plugins/kraft-lite/chains/default.json
    uv run pytest plugins/kraft-lite/tests -q
    test -f plugins/kraft-lite/LICENSE
    claude plugin validate plugins/kraft-lite --strict
    version=$(python3 -c "import json;print(json.load(open('plugins/kraft-lite/.claude-plugin/plugin.json'))['version'])")
    tag="kraft-lite--v$version"
    # `main` is force-pushed, so the tags are the only fixed points in the
    # published history: one already at this version means the bump is missing.
    # --exit-code says 2 for "no such tag", and anything else is a remote that
    # could not be reached -- which must not read as a clean bump.
    rc=0; git ls-remote --exit-code --tags lite "$tag" >/dev/null || rc=$?
    case $rc in
        0) echo "kraft-lite v$version is already published -- bump plugin.json"; exit 1 ;;
        2) ;;
        *) echo "cannot reach the lite remote: git ls-remote exited $rc"; exit 1 ;;
    esac
    git branch -D lite-publish 2>/dev/null || true
    git tag -d "$tag" 2>/dev/null || true
    git subtree split --prefix=plugins/kraft-lite -b lite-publish
    git tag "$tag" lite-publish
    # Atomic: a tag push that fails after main moved would leave the release
    # untagged, and the next force-push makes that commit unreachable.
    git push --force --atomic lite lite-publish:main "$tag"
    # Both point into the split history, which is disjoint from this repo's.
    git tag -d "$tag"
    git branch -D lite-publish

# Frontend typecheck + unit tests. `npm test` is vitest, which does NOT typecheck;
# CI's `npm run build` runs `tsc -b` and will fail on errors vitest sails past. Keep
# the two in step here, or the only way to find a type error is to spend a pipeline.
test-ui:
    cd frontend && npx tsc -b
    cd frontend && npm test

# Playwright e2e
e2e:
    cd frontend && npm run e2e

# Lint + format check
lint:
    uv run ruff check .
    uv run ruff format --check .

# Autofix lint + format
fix:
    uv run ruff check --fix .
    uv run ruff format .
