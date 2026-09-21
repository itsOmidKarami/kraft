# Testing guideline

Two tiers, a small set of shapes, and one procedure for proving a test pins
something. This is normative for `tests/` and `frontend/src/**/*.test.*`;
`dev/check_tests.py` enforces what can be checked mechanically (see
"Enforced mechanically" below).

## Two tiers

- **Unit (default, unmarked).** No real external CLI: `bd`, `claude`/agent
  harnesses, `gh`/`glab` and servers are mocked at their adapter seam. `git`
  is real (worktrees, rebases and submodules are the product), but a repo
  comes from the cached `make_repo`/`repo` fixture, never built from
  scratch. Unit tests cover every combination the code branches on, and are
  fast and deterministic. `tests/conftest.py`'s `fake_beads` autouse fixture
  installs the in-memory `FakeBeads` for every test and refuses a real `bd`
  reached through `kraft.adapters.beads`; the `_no_real_agent_binary`
  autouse fixture does the same for a real agent binary. Both fail loudly
  rather than swallow the mistake.
- **E2E (`@pytest.mark.e2e("<cli>")`).** A real CLI, used only to prove the
  contract Kraft assumes: args, subcommands and output shape. Keep it small:
  one or two tests per contract, never behaviour sweeps. If the binary is
  missing the test skips and names the binary; CI sets `KRAFT_E2E_REQUIRE`
  so that skip becomes a failure. Every `e2e` marker must name at least one
  CLI (`pytest_collection_modifyitems` in `tests/conftest.py` raises
  `UsageError` if it doesn't) — `dev/check_tests.py` catches the same defect
  statically, before collection.

A test that wants to run the same behaviour against both tiers takes one
parametrized fixture instead of two test bodies. `support.fake_beads` does
this for beads:

```python
from support.fake_beads import ON_FAKE_AND_REAL_BD

@ON_FAKE_AND_REAL_BD
async def test_intake_files_the_bead_in_the_work_items_repo(bd, tmp_path, database, run_dirs):
    ...
    assert row["bead_id"] in bd.ids(cwd=repo)
```

`ON_FAKE_AND_REAL_BD` parametrizes the `bd` fixture into a `[fake]` case
(unit tier, backed by the in-memory fake) and a `[bd]` case (marked
`e2e("bd")`, backed by the real CLI). One test body serves both; a `[bd]`
case that lost its `e2e("bd")` mark fails loudly (`tests/conftest.py`'s `bd`
fixture refuses to answer a `param == "bd"` request against the fake).

## Shape

- **One behaviour, one test.** When several tests differ only in inputs or
  expected values, use `@pytest.mark.parametrize` with readable `ids=`.
  Don't copy-paste bodies.

  ```python
  @pytest.mark.parametrize("after_days", [None, 0], ids=["none", "zero"])
  def test_archive_after_days_defaults(after_days, ...):
      ...
  ```

- **Setup lives in fixtures and factories, not inline.** The backend's
  shared ones live in `tests/conftest.py` and `tests/support/`, each with a
  docstring and a usage example:
  - `database` (async) — an open, migrated `db.Database` at `run_dirs.db`,
    closed at teardown. Write the test `async def` and take the fixture;
    don't hand-roll `asyncio.run(scenario())`.
  - `run_dirs` — `RunDirs(tmp_path / "run").ensure()`, what `database` and
    every `store` reader take alongside it.
  - `repo` — a committed copy of `tests/support/sample_repo`
    (`support.harness.make_repo`), never built from scratch.
  - `client` — a started `TestClient` on the Kraft app, hermetic under
    `tmp_path`. Options go on `@pytest.mark.api_client(...)`, not on the
    fixture's own arguments. `client` pulls `tests/api/conftest.py`'s `dist`
    fixture itself (`request.getfixturevalue("dist")`) whenever a test also
    lists `dist`, so it works whichever order the two are listed in
    (below).
  - `templates_dir` — `KRAFT_TEMPLATES_DIR` with every agent on the fake
    `claude`. Override it in a file that needs a different shape (an
    edited library or chain); `client` picks the override up
    because it takes `templates_dir` as an argument, not because of
    argument order.
  - `dist` (`tests/api/conftest.py`) — a built SPA (`index.html` and one
    asset) pinned as `KRAFT_FRONTEND_DIST`, for the api tier's SPA-shell
    tests.
  - `item_on(chain, node=None, *, repo=None, **kwargs)` — files a V1 work
    item in `database`, standing at `node`, and returns a
    `support.harness.Item`: `.row()`, `.status()`, `.events(type)`,
    `.sessions(node)`, `.worktree`, `await .session(sid, "node.step.task",
    status, ...)`.
  - `bd` — `support.fake_beads.Bd`: `.status(id, cwd=)`, `.ids(cwd=)`,
    `.block(id, blocker, cwd=)`, `.init(path)`, answered by the fake in the
    unit tier and the real CLI under `e2e("bd")` (see `ON_FAKE_AND_REAL_BD`
    above).
  - `app` / `stub_app` — the ASGI app and a fake poller `app.state`, for
    tests below the HTTP layer.

  Frontend setup lives in `frontend/src/testFixtures.ts`: `item()`,
  `detailItem()`, `QUICK`, `setPhoneWidth()` — factories that take
  overrides, not copy-pasted object literals. Add a factory there the
  second time a setup recurs.

- **A fixture that depends on another should pull it itself, not rely on
  argument order.** Wave 3a's `dist` fixture first only worked when listed
  before `client` in a test's signature — pytest sets fixtures up in
  argument order, so that was fragile and silently broke when someone
  reordered arguments. The fix: `client` calls
  `request.getfixturevalue("dist")` itself, right before it builds the test
  client, whenever a test also lists `dist` — so listing order between the
  two no longer matters. Prefer that shape for any new fixture that
  composes with another: pull, don't hope for order.

- **One file, one concern.** Split a file when it passes the line budget
  (below) or mixes concerns. Its name says what it covers.
- **No behaviour pinned twice across files.** Before writing a test, search
  for an existing one covering the same branch.
- **Frontend:** `it.each` / `describe.each`, shared render helpers and the
  `WorkItem` factories in `testFixtures.ts`. Playwright proves the
  UI↔server contract; vitest covers component behaviour. Don't duplicate
  between them.

## What counts as coverage

**"Real coverage, not just a number."** Line coverage is a signal, not
proof. A test only counts as a pin if it fails when the behaviour it names
breaks.

### How to prove a test pins something

1. Change the code under test so the behaviour the test names is wrong (a
   mutation): flip a comparison, drop a branch, change a default, delete a
   guard.
2. Run that one test with `just test <path> -k <name>`.
3. Confirm it fails — and fails for the reason you broke, not for an
   unrelated setup error. If it still passes, the test pins nothing; fix
   the test, not the changelog entry.
4. Revert the mutation.

Do this for every new or consolidated pin before you consider the work
done, and note the mutation in the commit or PR description (see the wave
reports in this repo's history for the convention: "mutation: `<the
change>` kills `<the test/case>`; applied, confirmed failing, reverted").

### Five vacuous shapes to watch for

- Asserting nothing (or only "no exception was raised").
- Re-implementing the subject in the test, so the assertion just re-derives
  the same computation.
- Pinning a case the product can never produce.
- Only ever asking the subject to succeed, never to refuse or fail.
- A test (or fixture) that skips itself out of existence, or sets up the
  subject to succeed on a shape the product never ships.

**Assert on populated values, not on "no error".** A `200` status with no
body assertion, or a bare `assert result` with no shape check, is close to
one of the five shapes above.

**Removing a test means accounting for it:** name what replaces it, or
state what it uniquely pinned — "nothing, proven by mutation" is a valid
answer, but it has to be checked, not assumed.

**Intent pins:** `docs/intent/*.md` reference test ids by name. Renaming or
parametrizing a pinned test means repointing it in the same change. Run
`just intent` and `tests/test_intent_origins.py`.

## Running

- `just test <paths>` (testmon), never raw pytest. `just test-ui` for the
  frontend.
- The full suite runs in CI, so push and read CI rather than running it
  locally.
- Only one heavy local test run at a time on a shared machine.

## Enforced mechanically

`dev/check_tests.py`, run in CI's `lint` job, next to
`dev/check_docs_coverage.py`. It checks what can be checked statically —
not "did you prove a mutation," which needs a human or an agent, but the
shape rules that don't:

- **(a)** every `pytest.mark.e2e` names at least one CLI.
- **(b)** no test file outside the e2e tier calls a real `bd`/`claude`/
  `gh`/`glab` binary via `subprocess`. This is a static belt-and-braces
  check; the runtime guard (`tests/conftest.py`'s autouse fixtures) is what
  actually stops it at test time.
- **(c)** a per-file line budget for `tests/**`, with an explicit allowlist
  (`LINE_BUDGET_ALLOWLIST`, a dict at the top of `dev/check_tests.py` —
  there is no separate allowlist file) naming today's over-budget files and
  their current size. An allowlisted file may shrink but not grow past its
  recorded size; a new file must be under budget from the start. The
  allowlist can't drift stale either: an entry for a file that's shrunk to
  budget or under, or whose recorded ceiling now sits more than a small
  margin above the file's real size, fails the check until it's tightened
  or removed.
- **(d)** every `def test_` has an assert, a `pytest.raises`/`pytest.warns`,
  or delegates to a same-module helper that does. A handful of tests where
  "did not raise" is genuinely the only honest assertion are named in
  `EXPECTATION_ALLOWLIST` (same file), each with an inline reason; an entry
  for a test id that no longer exists fails the check.

A parse failure in the checker is a failure, not a skip — a checker that
can't read a file must not read as "passing" (this is the same bug class
the docsite coverage check guards against: silent success is worse than a
loud, wrong failure).

Run it directly: `uv run python dev/check_tests.py`, or `just check-tests`.
