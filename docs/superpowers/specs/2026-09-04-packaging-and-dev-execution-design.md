# Packaging and execution: `kraft` command + dev instance

Date: 2026-09-04
Beads: Kraft-bql

## Problem

Kraft only runs as `python -m kraft` from a git checkout. Three things are wired to
that assumption:

- `api.py` and `__main__.py` derive `TEMPLATES_DIR` and `DEFAULT_FRONTEND_DIST` from
  `Path(__file__).resolve().parents[2]`, which is the repo root only while the source
  tree is the install. Under `site-packages` it points somewhere meaningless.
- The run directory defaults to `.kraft-run`, relative to the current working
  directory, so where state lands depends on which shell tab launched the server.
- There is no way to exercise the UI against realistic data without spending real
  agent tokens. `fixtures/fake-claude.sh` exists but only tests reach it.

Two outcomes are wanted: an installed `kraft` command for real use, and a `just dev`
instance with a pre-filled database and mocked agents.

## Non-goals

- Docker or any container image. Kraft shells out to `git worktree` and to agent CLIs
  that hold host credentials; a container buys isolation Kraft actively does not want.
- Publishing to PyPI, and the CI wheel build that implies.
- A second mock agent. The test fake is the dev fake.
- Multi-user or remote deployment. Binding off loopback already requires a password
  (`__main__._bind`); that stays the whole story.

## 1. Home resolution

`kraft.paths` gains:

```python
def kraft_home() -> Path:
    return Path(os.environ.get("KRAFT_HOME") or Path.home() / ".kraft").expanduser()
```

Defaults derived from it:

| Thing | Today | After |
|---|---|---|
| run dir | `.kraft-run` relative to CWD | `$KRAFT_HOME/run` |
| templates/config dir | `<repo>/templates` | `$KRAFT_HOME/templates` |
| frontend dist | `<repo>/frontend/dist` | packaged `kraft/_bundled/web` |

The existing environment overrides — `KRAFT_RUN_DIR`, `KRAFT_TEMPLATES_DIR`,
`KRAFT_FRONTEND_DIST` — keep precedence over these defaults. That is what keeps the
test suite (which sets them per-test) unaffected, and it is what `just dev` uses to
put a dev instance somewhere other than the real home.

`_REPO_ROOT` is deleted from both `api.py` and `__main__.py`. Nothing in the runtime
may resolve a path by counting parent directories of `__file__` again.

The home layout, all created on demand:

```
~/.kraft/
  templates/         registry.yaml, policy.yaml, repos.yaml, access.yaml, *.yaml templates
  run/               orchestrator.db, index.db, logs/, results/, worktrees/
```

`templates/` stays a plain directory of YAML because design 02 §4.7 makes the Settings
screens an editor for files, not a front end for a config table. A user who wants
history can `git init` in it.

## 2. The `kraft` console script

`pyproject.toml` gains:

```toml
[project.scripts]
kraft = "kraft.cli:main"
```

`kraft/cli.py` holds what `__main__.py` holds today (`_bind`, the `uvicorn.run` call)
plus first-run seeding:

- If `$KRAFT_HOME/templates` does not exist, copy `kraft/_bundled/templates/*` into it
  and print the path once, so the user knows which files the UI is about to edit.
- If it exists, leave it alone. Upgrades never overwrite an edited config; a new
  template shipped by a later version is a problem for a later version.
- The copy lands under a staging name and is renamed into place, so an interrupted
  seed cannot leave a partial config the next start mistakes for a complete one.
- No bundle *and* no config is a hard exit with an explanation, not a
  `FileNotFoundError` on `registry.yaml` three layers down in lifespan.
- `access.yaml` is not bundled (it is gitignored, per-machine, and holds a password
  hash); `config.load_access` already returns defaults for a missing file.

`__main__.py` becomes a re-export so `python -m kraft` and `kraft` are the same code
path — the justfile and the tests keep using the module form.

### Bundling

`src/kraft/_bundled/` holds `web/` (built SPA) and `templates/` (default YAML). Both
are gitignored build outputs, populated by a justfile recipe:

```
install:
    cd frontend && npm run build
    rm -rf src/kraft/_bundled
    mkdir -p src/kraft/_bundled
    cp -R frontend/dist src/kraft/_bundled/web
    cp -R templates src/kraft/_bundled/templates
    uv tool install --from . kraft --force
```

`[tool.setuptools.package-data]` includes `kraft/_bundled/**` so the copies land in the
installed distribution.

Deliberate shortcut: the copy is a justfile step, not a setuptools build hook. A build
run any other way produces a package with no SPA — `api.py` already degrades to
API-only when the dist directory is absent, so the failure is visible rather than
corrupting anything. Marked with a `ponytail:` comment. Upgrade path is a
`build_py` subclass or `hatchling` hook, worth doing the day a wheel is built anywhere
but this laptop.

Result: `uv tool install` once, then `kraft` from any directory, serving the SPA and
API on the bind address in `~/.kraft/templates/access.yaml`.

## 3. Mocked agents in dev

`fixtures/bin/claude` is a symlink to `../fake-claude.sh`. `just dev` prepends
`fixtures/bin` to `PATH`.

This mocks every agent hook at once, whatever command the registry names, with no dev
copy of `registry.yaml` to drift from the real one. `registry.yaml` keeps saying
`command: claude`, exactly as in production.

Two additions to `fake-claude.sh`: when the `-p` instruction contains the token
`KRAFT_FAIL`, exit non-zero without writing a result file. Mode today comes from
`KRAFT_FAKE_CLAUDE`, which is per-server and therefore cannot vary across work items
inside one dev instance; taking it from the instruction lets the seed script drive
several outcomes against a single running server. `KRAFT_SLOW` does the same for a
delay, which is what makes "pause a running item" a wait rather than a race — pause
is a 409 on anything but an `active` item. The existing env modes
(`fix`/`noop`/`slow`) are untouched.

## 4. The dev instance

`just dev` runs the backend and vite with a repo-local home:

```
KRAFT_HOME=.dev
PATH=$PWD/fixtures/bin:$PATH
```

`.dev/` is gitignored. A `_dev-home` prerequisite recipe builds it: templates copied
from the tracked `templates/` (minus `access.yaml`, which holds this machine's bind
address and password hash and must never reach an unauthenticated dev instance), and
the seed repo, which has to exist before the server starts because `KRAFT_BD_CWD`
points at it and intake shells out to `bd` there on the very first work item.

`just start` is deleted. It built the SPA and served it from a checkout, which is
exactly what `just install` + `kraft` now does properly.

`just dev-seed` runs `dev/seed.py` against the running server (httpx is already a dev
dependency). It:

1. Creates `.dev/repo` — `git init`, a `calc.py` whose `add` returns `a - b` (the bug
   `fake-claude.sh` knows how to fix), a `test_calc.py` that catches it, one commit.
2. `POST /repos` to register it.
3. `POST /work-items` several times, with instructions chosen so the items settle in
   different states: completed, waiting at a gate, failed (via `KRAFT_FAIL`), and
   paused — the last by waiting for a running session on a `KRAFT_SLOW` item, then
   `POST /work-items/{id}/pause`.
4. Polls `GET /work-items` until each has reached its intended state, then prints a
   summary table. Exits non-zero if any item is still moving after a timeout — a seed
   that silently produces four identical rows is worse than no seed.

`just dev-reset` is `rm -rf .dev`.

The script is idempotent in the only sense that matters: it is meant to run against a
fresh `.dev`. Re-running it on a populated instance just adds more work items.

## Testing

- `tests/test_cli.py`: `kraft_home()` honours `KRAFT_HOME` and falls back to
  `~/.kraft`; first-run seeding copies bundled templates into an empty home and does
  not overwrite an existing `registry.yaml`. Uses `tmp_path`, no server.
- The existing suite is the regression check for the default changes — every test that
  cares already sets `KRAFT_RUN_DIR` / `KRAFT_TEMPLATES_DIR` explicitly. Any test that
  turns out to depend on the old repo-root default is a test that was depending on the
  bug.
- `dev/seed.py` is its own check: it fails loudly when the states it asks for are not
  the states it gets.
- Manual: `just install && kraft` on a machine with no `~/.kraft`, confirm the SPA
  loads and Settings shows the seeded config paths.

## Risks

- **Stale bundle.** `just install` without a fresh `npm run build` ships an old SPA.
  The recipe always builds, so the risk is someone installing by another route.
- **Two homes on one machine.** A dev instance left running against `.dev` and a real
  `kraft` both bind 8765. Second one fails to bind with a clear OSError; not worth
  guarding beyond that.
- **`fake-claude.sh` grows a second consumer.** Tests and dev now both depend on it, so
  a change for dev can break tests. That is the point — it keeps the fake honest.
