# Kraft dev tasks. Run `just` to list.

set shell := ["bash", "-uc"]

_default:
    @just --list

# Install backend + frontend deps, then the agent skills (`setup-plugins`)
setup:
    uv sync
    cd frontend && npm ci
    @just setup-plugins

# superpowers and impeccable, for every agent this repo is worked on with.
#
# Claude Code: .claude/settings.json turns both plugins on, but committing
# `enabledPlugins` does not download a plugin from an outside source, so each
# machine installs them once, into ~/.claude, which every checkout and worktree
# shares.
#
# Every other agent Kraft runs (Codex, Cursor, Gemini CLI, OpenCode, Amp,
# Antigravity) and GitHub Copilot reads skills from .agents/skills/. The
# `skills` CLI copies them there; they are gitignored, and only beads is
# committed in that folder, so each checkout and worktree installs its own.
#
# Kraft runs `just setup` in every worktree, more than once, so each half skips
# what is already installed, and a missing tool or a failed download is a
# warning, never a failed setup: neither is needed to build or test Kraft. To
# refresh: `claude plugin update <id>` and `npx skills update -p`.
[doc("Install superpowers and impeccable for Claude Code and every other agent")]
setup-plugins:
    #!/usr/bin/env bash
    set -uo pipefail
    # Each download gets a time limit, so an unreachable host costs a worktree
    # setup minutes at most rather than hanging it. Stock macOS has no
    # `timeout`, so there it runs unlimited.
    limit() {
        if command -v timeout >/dev/null; then timeout 300 "$@"; else "$@"; fi
    }
    claude_plugins() {
        local failed=0
        limit claude plugin marketplace add anthropics/claude-plugins-official >/dev/null || failed=1
        limit claude plugin marketplace add pbakaus/impeccable >/dev/null || failed=1
        limit claude plugin install superpowers@claude-plugins-official --scope project || failed=1
        limit claude plugin install impeccable@impeccable --scope project || failed=1
        return $failed
    }
    agent_skills() {
        local source
        for source in obra/superpowers pbakaus/impeccable; do
            DISABLE_TELEMETRY=1 NPM_CONFIG_UPDATE_NOTIFIER=false limit npx -y skills@1.7.4 \
                add "$source" --skill '*' \
                --agent codex cursor gemini-cli opencode amp antigravity github-copilot \
                --copy -y >/dev/null || return 1
        done
    }
    # Read the install record rather than start `claude plugin list`: Kraft runs
    # this on every dispatch, so the already-installed path has to be cheap.
    installed="${CLAUDE_CODE_PLUGIN_CACHE_DIR:-$HOME/.claude/plugins}/installed_plugins.json"
    if ! command -v claude >/dev/null; then
        echo "claude not installed; skipping the Claude Code plugins"
    elif grep -qs '"superpowers@claude-plugins-official"' "$installed" &&
        grep -qs '"impeccable@impeccable"' "$installed"; then
        echo "Claude Code plugins already installed"
    elif ! claude_plugins; then
        echo "warning: could not install the Claude Code plugins; run 'just setup-plugins' to retry" >&2
    fi
    if ! command -v npx >/dev/null; then
        echo "npx not installed; skipping the skills for agents other than Claude Code"
    elif [[ -f .agents/skills/using-superpowers/SKILL.md && -f .agents/skills/impeccable/SKILL.md ]]; then
        echo "Agent skills already in .agents/skills/"
    elif agent_skills; then
        echo "Installed superpowers and impeccable into .agents/skills/"
    else
        echo "warning: could not install the skills into .agents/skills/; run 'just setup-plugins' to retry" >&2
    fi

# Install deps including semantic search (downloads a ~130MB model on first
# search). Without this, /search still works in fts mode.
[doc("Install deps plus semantic search (a ~130MB model on first search)")]
setup-vector:
    uv sync --extra vector
    cd frontend && npm ci

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

# A dev home starts as a copy of the tracked config/, plus the throwaway repo
# beads and the seeded work items live in. Copied, not pointed at: the Settings
# screens write to this directory, and dev edits must not land in the repo's real
# config. access.yaml never comes along — it is this machine's bind address and
# password hash, and a dev instance must stay on unauthenticated loopback.
_dev-home:
    @mkdir -p .dev
    @[ -d .dev/config ] || {{ '{' }} [ -d .dev/templates ] && mv .dev/templates .dev/config; {{ '}' }} || cp -R config .dev/config
    @rm -f .dev/config/access.yaml
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
    api=$!
    cd frontend && npm run dev &
    # The backend's own `kraft: http://127.0.0.1:{{dev_port}}` line prints after
    # vite's banner, so it read as the address to open. It isn't: that port serves
    # frontend/dist, the last `npm run build` (or no SPA at all), not the code
    # being edited. Once the API answers, say which one is the UI. The answer has
    # to name this checkout's run dir: a backend that refused a taken port leaves
    # somebody else's server answering there. Vite moves past a taken 5173 on its
    # own, so its Local line, not this one, has the final say on the port. The
    # server reports its run dir symlink-resolved; `just -f` through a symlink
    # does not, so resolve ours the same way.
    run_dir="$(cd "{{justfile_directory()}}/.dev" && pwd -P)/run"
    for _ in $(seq 150); do
        kill -0 "$api" 2>/dev/null || break
        if curl -sf http://127.0.0.1:{{dev_port}}/api/health 2>/dev/null \
            | grep -F "\"run_dir\":\"$run_dir\"" >/dev/null; then
            echo "just dev: the UI is vite's Local URL above (:5173 unless taken). :{{dev_port}} is the API, not the UI."
            break
        fi
        sleep 0.2
    done
    wait

# Fill a running dev instance with work items in every interesting state; --rich for ~40 items, extreme text, a 30-node chain, three repos and a wide review
dev-seed *args: _dev-home
    {{dev_env}} uv run python dev/seed.py {{args}}

# The console against the dev instance (127.0.0.1:8766): start `just dev` or `just api` first
dev-console: _dev-home
    {{dev_env}} uv run python -m kraft console

# Retake .github/assets/console.png: the console headless against the dev instance, which
# `just api` and `just dev-seed --rich` must have filled (args: --version X.Y.Z, --size 120x34)
[doc("Docs: retake the console screenshot from the seeded dev instance")]
console-screenshot *args: _dev-home
    cd frontend && [ -d node_modules ] || npm ci
    cd frontend && npx playwright install chromium
    {{dev_env}} uv run python dev/console_screenshot.py {{args}}

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
[doc("Build the SPA and default config into the package tree")]
bundle:
    cd frontend && npm run build
    rm -rf src/kraft/_bundled
    mkdir -p src/kraft/_bundled
    cp -R frontend/dist src/kraft/_bundled/web
    cp -R config src/kraft/_bundled/config
    # The agent skills live in plugins/kraft/ so they can be published beside
    # kraft-lite in one marketplace. An installed Kraft has no plugins/ beside
    # it, so they ride into the wheel here with the SPA.
    cp -R plugins/kraft/skills src/kraft/_bundled/plugin-skills
    # never ship a local access.yaml or notify.yaml: one holds this machine's
    # password hash, the other a webhook URL that usually embeds a bearer
    # token. `cp -R` does not know either is secret -- `.gitignore` only keeps
    # them out of the commit, not out of the wheel or the homes it seeds.
    rm -f src/kraft/_bundled/config/access.yaml
    rm -f src/kraft/_bundled/config/notify.yaml

# Install `kraft` as a real command (then just run `kraft` from anywhere).
# State lands in ~/.kraft, seeded from config/ on first run.
[doc("Install `kraft` as a real command; state in ~/.kraft")]
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
# nothing was collected at all. A path argument that matches nothing must not
# look like a run that found nothing wrong, so we grep for it and fail
# ourselves, when args were given. The same goes for a run testmon emptied:
# "collected N items / N deselected / 0 selected", or with -q only
# "N deselected in 0.1s". Nothing ran, so nothing was shown to pass.
#
# Testmon follows Python execution only. A test whose real input is a data
# file -- a harness YAML, fixtures/fake-claude.sh, a script under
# tests/support run as a subprocess, config/, a skill's .md, the sample
# repo, docs/intent -- is deselected when that file changes. So when one of
# them differs from the merge base with origin/main (committed on this branch,
# staged, unstaged or untracked), named paths run with --no-testmon, saying
# which files made it; a bare `just test` only warns, since its fallback would
# be the whole suite. src/kraft/_bundled is gitignored and built from
# config/ and plugins/kraft/skills, which are watched instead.
[positional-arguments]
[doc("Backend tests affected by your changes (testmon); --no-testmon for all")]
test *ARGS:
    #!/usr/bin/env bash
    set -uo pipefail
    log=$(mktemp -t kraft-test.XXXXXX)
    trap 'rm -f "$log"' EXIT
    mode=--testmon
    case " $* " in *" --no-testmon "*) mode= ;; esac
    if [ -n "$mode" ]; then
        inputs=(src/kraft ':(glob,exclude)src/kraft/**/*.py' fixtures config tests/support docs/intent plugins/kraft/skills)
        base=$(git merge-base HEAD origin/main 2>/dev/null || echo HEAD)
        changed=$({ git diff --name-only "$base" -- "${inputs[@]}"; git ls-files --others --exclude-standard -- "${inputs[@]}"; } 2>/dev/null | sort -u)
        if [ -n "$changed" ] && [ -n "$*" ]; then
            echo "just test: testmon cannot see a change to these, so it could deselect the tests that read them -- running the given args with --no-testmon:" >&2
            printf '%s\n' "$changed" | head -20 | sed 's/^/  /' >&2
            mode=--no-testmon
        elif [ -n "$changed" ]; then
            echo "warning: testmon cannot see a change to these, so it may deselect tests that read them -- name the paths, or run 'just test --no-testmon', before trusting a green run:" >&2
            printf '%s\n' "$changed" | head -20 | sed 's/^/  /' >&2
        fi
    fi
    COVERAGE_CORE=ctrace uv run pytest ${mode:+"$mode"} "$@" 2>&1 | tee "$log"
    status=${PIPESTATUS[0]}
    if [ -n "$*" ] && grep -qE '^collected 0 items$' "$log"; then
        echo "error: just test collected 0 items for the given args -- treating as a failure (Kraft-1v6ow)" >&2
        exit 1
    fi
    if [ -n "$*" ] && grep -qE '^collected [0-9]+ items / [0-9]+ deselected / 0 selected$|^(=+ )?[0-9]+ deselected(, [0-9]+ warnings?)? in [0-9.]+s' "$log"; then
        echo "error: every test the given args name was deselected (by testmon, or by -k/-m), so none ran -- pass --no-testmon to run them" >&2
        exit 1
    fi
    exit "$status"

# The unit tier on another Python, in its own environment (.venv-<version>) so
# it never disturbs the default one: `just test-py 3.12`, or add pytest args,
# `just test-py 3.13 -k search`. CI runs the whole supported range; use this to
# reproduce one version's failure. Full run, no testmon: its data is per-env.
[positional-arguments]
[doc("Unit tier on another Python: just test-py 3.12 [pytest args]")]
test-py version *ARGS:
    #!/usr/bin/env bash
    set -uo pipefail
    v=$1
    shift
    UV_PROJECT_ENVIRONMENT=".venv-$v" uv run --python "$v" pytest -m "not e2e" -n logical "$@"

# Check the intent tree: every enforced-by pin resolves, and list what nothing pins
intent:
    uv run python -m kraft.intent

# Move every enforced-by pin on OLD (or OLD[case]) to NEW; refuses when NEW does not collect
intent-repoint OLD NEW:
    uv run python -m kraft.intent --repoint "{{OLD}}" "{{NEW}}"

# Refresh src/kraft/prices.json from models.dev (Kraft-wz83s). Never fetched at
# runtime -- this is the only thing that ever hits the network for it. Review the
# diff before committing: a price change is worth a look, not a rubber stamp.
[doc("Refresh src/kraft/prices.json from models.dev")]
refresh-prices:
    uv run python dev/refresh_prices.py

# Check the test suite against docs/testing.md's mechanical rules: e2e markers
# name a CLI, no unit test reaches a real bd, agent or forge CLI, the per-file line
# budget, every test has an expectation, no helper body is copied across test
# files. Both testpaths, kraft-lite's included.
[doc("Check the test suite against docs/testing.md's mechanical rules")]
check-tests:
    uv run python dev/check_tests.py

# How big and how repetitive tests/ is: functions, collected cases, lines,
# verbatim-repeat lines, duplicated helpers, densest modules. `--json` for tools.
[doc("Print the shape of tests/: size, repeats, duplicated helpers, densest modules")]
shape-report *ARGS:
    uv run python dev/test_shape_report.py {{ARGS}}

# Frontend typecheck + unit tests. `npm test` is vitest, which does NOT typecheck;
# CI's `npm run build` runs `tsc -b` and will fail on errors vitest sails past. Keep
# the two in step here, or the only way to find a type error is to spend a pipeline.
#
# node_modules is gitignored, so a fresh worktree (every Kraft worker gets one)
# has none, and `npx tsc` would silently fetch an unrelated `tsc` package from
# the registry instead of failing. Install first; the cost is only on the first
# run in a worktree.
[doc("Frontend typecheck + unit tests")]
test-ui:
    cd frontend && [ -d node_modules ] || npm ci
    cd frontend && npx tsc -b
    cd frontend && npm test

# Kraft-m2wru: launch every (harness, model, effort) the shipped library uses
# once against the real CLI. Spends a few cents. Tests run with a temp HOME, so
# a `claude login` is invisible to them: export ANTHROPIC_API_KEY or
# CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`) first. The pre-commit hook
# runs this when a commit touches config/ or the bundled harnesses.
[doc("Launch every shipped (harness, model, effort) on the real CLI; spends a few cents")]
smoke-models:
    @[ -n "${ANTHROPIC_API_KEY:-}${CLAUDE_CODE_OAUTH_TOKEN:-}" ] || { echo "smoke-models: export ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN (claude setup-token) -- tests use a temp HOME, so claude login is not seen" >&2; exit 1; }
    KRAFT_E2E=1 KRAFT_E2E_REQUIRE=claude just test tests/test_shipped_models.py -k real_cli --no-testmon

# The pre-commit hook's door to smoke-models: a Kraft worker or a shell with no
# credential skips it loudly instead of blocking the commit. smoke-models stays strict.
[doc("smoke-models for the pre-commit hook: skips without a credential")]
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

# The UI contract: ~90 behaviours of the built SPA (the sidebar's pin, Esc and focus,
# the item header's cards, review, the document viewer, tooltips) and the icon-only
# audit (every icon-only control has a name and a tooltip), in a real browser on a
# mocked /api. No Kraft server; it builds the SPA and serves it with `vite preview`.
# CONTRACT_PORT moves it off 4327. Pass Playwright flags after it: -g "sidebar".
[doc("UI contract and icon audit: the SPA's behaviours in a browser on a mocked API")]
ui-contract *args:
    cd frontend && [ -d node_modules ] || npm ci
    cd frontend && npx playwright install chromium
    cd frontend && npx playwright test -c e2e/contract/playwright.config.ts {{args}}

# The dashboard demo: the web UI on the UI contract's mocked API, no server or agents (http://localhost:5174/demo/)
demo:
    cd frontend && [ -d node_modules ] || npm ci
    cd frontend && npm run demo

# The dashboard demo's smoke suite: builds the demo, serves it with `vite preview` on :4328
# (DEMO_PORT moves it), boots it, and follows every things-to-try Show me to its target.
# Pass Playwright flags after it: -g "gate".
[doc("Dashboard demo smoke: it boots, and every Show me finds its target")]
demo-smoke *args:
    cd frontend && [ -d node_modules ] || npm ci
    cd frontend && npx playwright install chromium
    cd frontend && npx playwright test -c e2e/demo/playwright.config.ts {{args}}

# Retake the docs screenshots that come from the dashboard demo (frontend/e2e/docs-shots/):
# builds the demo, serves it on :4329 (DEMO_PORT moves it) and writes each picture into
# .github/assets/. Pass Playwright flags after it: -g storage takes one.
[doc("Docs: retake the screenshots taken from the dashboard demo (args: Playwright flags, -g NAME)")]
docs-screenshots *args:
    cd frontend && [ -d node_modules ] || npm ci
    cd frontend && npx playwright install chromium
    cd frontend && npx playwright test -c e2e/docs-shots/playwright.config.ts {{args}}

# Playwright e2e against a fixture server you started (frontend/e2e/README.md)
e2e:
    cd frontend && npm run e2e

# `just e2e` assumes a human already started the fixture server (see
# frontend/e2e/README.md) -- not a contract a worker can meet. This builds the
# SPA, starts serve.py in the background, waits for it to print its base URL,
# points Playwright at it, and tears the server down on exit either way.
# Mirrors the `playwright` job in .github/workflows/test.yml; that job is the
# proof this sequence works, run on every frontend-touching PR.
[doc("Playwright e2e with its fixture server: what CI's playwright job runs")]
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

# Regenerate frontend/src/types/vocab.generated.ts from kraft.vocab
[doc("Regenerate the generated TS status vocabulary")]
vocab:
    uv run python dev/gen_vocab.py

# Rewrite tests/api/api_shapes.json, the keys each GET route the web UI reads
# answers with, which frontend/demo/apiShapes.test.ts holds the mocked API to
[doc("Rewrite the API response-shape table from the real server")]
shapes:
    WRITE_API_SHAPES=1 just test tests/api/test_api_shapes.py --no-testmon

# Regenerate src/kraft/templates/lucide_icons.txt from the installed lucide-react
# (the icon names a template's `icon:` is linted against). Run after every bump.
[doc("Regenerate the lucide icon list after a lucide-react bump")]
icons:
    uv run python dev/gen_icon_names.py

# vscode/ checks: the committed schemas match the pydantic models, and the
# extension typechecks and passes its unit tests.
[doc("VS Code extension: schemas current, typecheck, unit tests")]
test-vscode: schemas
    git diff --exit-code -- vscode/schemas
    cd vscode && [ -d node_modules ] || npm ci
    cd vscode && npm run typecheck
    cd vscode && npm test

# Retake the Marketplace screenshots in vscode/media/: packages the extension,
# runs it in a real VS Code against a seeded Kraft with fake agents (on Linux,
# under xvfb-run). Written to vscode/media/, the docs' copies in .github/assets/vscode-*.png follow.
[doc("VS Code extension: retake the Marketplace screenshots (args: --out DIR, --vsix FILE, --keep)")]
vscode-screenshots *args:
    cd vscode && [ -d node_modules ] || npm ci
    cd vscode/dev/screenshots && [ -d node_modules ] || npm ci
    node vscode/dev/screenshots/shot.mjs {{args}}

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
# Keep this in step with .github/workflows/test.yml's `lint` and `test` jobs
# by hand -- there's no test enforcing it since GitLab CI's config (and the
# test that checked it) was retired. It covers those two jobs only: the e2e,
# frontend, vscode, playwright and kraft-lite jobs have recipes of their own
# (CONTRIBUTING.md, "What CI checks").
[doc("CI's lint and test jobs, in order, on the full suite (no testmon)")]
ci-test:
    uv run ruff check .
    uv run ruff format --check .
    uv run python dev/check_docs_coverage.py
    uv run python dev/check_docs_walls.py
    uv run python dev/check_docs_redirects.py
    uv run python dev/check_docs_landings.py
    uv run python dev/check_docs_shell.py
    uv run python dev/check_docs_ledes.py
    uv run python dev/check_docs_added_in.py
    uv run python dev/check_tests.py
    uv run pytest -m "not e2e" -n logical
    uv run python -m kraft.intent

# Preview the docs site with live reload at http://localhost:3000/kraft/
docs:
    cd docsite && [ -d node_modules ] || npm ci
    cd docsite && npm run dev

# Build the published site (latest release at /, older majors at /vN/, main at
# /next/, each with its dashboard demo at <base>demo/) and serve it at
# http://localhost:8000/.
# python's server lists a page's folder and 404s the page itself
# (/next/guides/x is guides/x.html), so the deep pages need a server that maps
# /a/b to a/b.html, as GitHub Pages does.
[doc("Build every docs version and serve them at http://localhost:8000/")]
docs-site:
    cd docsite && [ -d node_modules ] || npm ci
    cd frontend && [ -d node_modules ] || npm ci
    rm -rf docsite/dist-site
    dev/build_docs_site.sh docsite/dist-site
    python3 dev/check_llm_docs.py docsite/dist-site
    python3 -m http.server 8000 -d docsite/dist-site
