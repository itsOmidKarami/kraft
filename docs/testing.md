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
  reached through `kraft.adapters.beads`. The `_no_real_agent_binary`
  autouse fixture (`tests/support/real_binaries.py`) does the same for every
  agent CLI and for `gh`/`glab`, by whatever road: it puts a refusing stub for
  each name first on `PATH`, so `subprocess`, `asyncio`, `os.system`,
  `sh -c "claude ..."`, `env claude` and a `python -m kraft` child all reach
  the stub; it checks `subprocess.Popen` for an installed real binary reached
  by its path; and `run_task` refuses one by name in the test itself. A
  server child (`support.server.child_env`) gets the same stubs, plus a stub
  `bd`. Every one of them fails the test, at the call or at teardown, rather
  than swallow the mistake: Kraft degrades on a failed CLI, so a refusal it
  turned into a warning still counts. A test marked `e2e("<cli>")` gets that
  CLI back; `@pytest.mark.real_executor` turns the agent guard off, for a
  test that drives the real executor on purpose. A test that proves the
  guard fires takes `real_binary_guard` and calls `.take()` on what it
  refused.
- **E2E (`@pytest.mark.e2e("<cli>")`).** A real CLI, used only to prove the
  contract Kraft assumes: args, subcommands and output shape. Keep it small:
  one or two tests per contract, never behaviour sweeps. If the binary is
  missing the test skips and names the binary; CI's e2e job sets
  `KRAFT_E2E_REQUIRE=bd,docker,podman`, so for those three that skip
  becomes a failure. Every `e2e` marker must name at least one
  CLI (`pytest_collection_modifyitems` in `tests/conftest.py` raises
  `UsageError` if it doesn't) — `dev/check_tests.py` catches the same defect
  statically, before collection. A test naming a real agent (`claude`)
  also needs `KRAFT_E2E=1`, since it spends tokens, and CI's e2e job has no
  agent credential, so those skip there. The one that matters,
  `tests/test_shipped_models.py`, runs locally instead: `just smoke-models`,
  which the pre-commit hook calls (through `just smoke-models-hook`) when a
  commit touches `config/` or `src/kraft/harnesses/`. The hook prints
  `SKIPPED:` and lets the commit through in a Kraft worker or with no
  credential set; `just smoke-models` itself still fails without one.

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

- **One behaviour, one test; one case, one row.** A fix to a behavior that
  already has a test adds a `parametrize` case to that test, with a readable
  `id`, not a new function. A new function is for a new behavior. When you
  find yourself writing `test_x_with_a_malformed_file`,
  `test_x_with_invalid_utf8`, `test_x_with_permission_denied`, that is one
  behavior ("x reports a bad file cleanly") and three rows.

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
    fixture's own arguments; a table whose rows need different options
    (a bind, a peer) passes the same dict per row with
    `parametrize("client", [...], indirect=True)`, merged over the marks.
    `client` pulls `tests/api/conftest.py`'s `dist`
    fixture itself (`request.getfixturevalue("dist")`) whenever a test also
    lists `dist`, so it works whichever order the two are listed in
    (below).
  - `templates_dir` — `KRAFT_CONFIG_DIR` with every agent on the fake
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

  Frontend setup lives in the area's own testkit under
  `frontend/src/ng/<area>/`: a `testkit.tsx`, `fixture.ts` or
  `testSupport.tsx` beside the tests (`ng/item/testkit.tsx` has `detail()`
  and `stubFetch()`; `ng/library/fixture.ts` a library draft; there are more
  under `ng/settings/`, `ng/harnesses/`, `ng/phone/areas/`, `ng/templates/` and
  `ng/item/draft/`) —
  factories that take overrides, not copy-pasted object literals. Add a
  factory to the nearest one the second time a setup recurs, not to a new
  top-level file. `frontend/src/testFixtures.ts` is the older shared set that
  a few tests still import; don't add to it. `frontend/README.md` lists the
  testkits.

- **A fixture that depends on another should pull it itself, not rely on
  argument order.** The `dist` fixture first only worked when listed
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
- **Frontend:** `it.each` / `describe.each`, and the render helpers and
  factories in the area's testkit. Playwright proves the UI↔server contract;
  vitest covers component behaviour. Don't duplicate between them. The
  UI contract (`frontend/e2e/contract/`) is a third layer: the built SPA
  in a browser on a mocked API, run by CI; `frontend/README.md` says when
  each of the three is required.

## What counts as coverage

**"Real coverage, not just a number."** Line coverage is a signal, not
proof. A test only counts as a pin if it fails when the behaviour it names
breaks.

### How to prove a test pins something

1. Change the code under test so the behaviour the test names is wrong (a
   mutation): flip a comparison, drop a branch, change a default, delete a
   guard.
2. Run that one case: `just test <path> -k 'name and case_id'`.
3. Confirm it fails — and fails for the reason you broke, not for an
   unrelated setup error. If it still passes, the test pins nothing; fix
   the test, not the changelog entry.
4. Revert the mutation.

Do this for every new or consolidated pin before you consider the work
done, and note the mutation in the commit or PR description: "mutation:
`<the change>` kills `<the test/case>`; applied, confirmed failing,
reverted". [#258](https://github.com/itsOmidKarami/kraft/pull/258) shows it
as a table.

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
answer, but it has to be checked, not assumed. A fold declares its whole
cluster in one wildcard line (the rule is below) and proves one mutation per
row.

CI holds a pull request to that. `dev/check_removals.py`, run by the
`removals declared` job, fails when the PR deletes a test function (or a
frontend test file) or drops an intent `## REQ` heading that its body does
not list under a `Removed tests` or `Removed requirements` heading, one id
per line with the reason after it:

```markdown
## Removed tests
- tests/test_old.py::test_gone -- replaced by tests/test_new.py::test_here
- tests/test_dead_file.py -- the whole file went with the feature
- tests/test_notify.py::test_get_notify_with_* -- folded into tests/test_notify.py::test_a_bad_notify_file_is_reported_cleanly[...]

## Removed requirements
- some-req-name -- superseded by other-req-name
```

A renamed test counts as removed plus added, so list its old id. A deleted
file may be listed by its path; a file that only loses some tests may not.
A parametrize case the PR drops counts as a removal too
(`tests/test_x.py::test_y[refused]`, with ids read off the source where it
spells them out), and so does a test it newly marks `@pytest.mark.skip`,
listed by its own id; a test removed whole is declared by its id, cases and
all. The heading may end in a colon. The failure prints the missing block ready to paste. Edit the body, then
re-run the job: it reads the body fresh, not from the push that triggered
it. This exists because a PR once silently reverted two merged PRs with CI
green — their tests left with them (Kraft-79382).

A fold declares its cluster with a wildcard, so consolidation is one line per
cluster: an id whose test name ends in `*` covers every removed test in that
same file whose id starts with the rest (cases included, `[` read literally).
The path stays literal, since a wildcard across files would hide exactly the
revert this job exists to catch, and a deleted file is still declared by its
plain path. The prefix must be longer than `test_`, and the line must name the
replacement as a `path::test` that exists at HEAD (a `[case]` or `[...]`
suffix is fine). A wildcard that is refused declares nothing, and one that
matches no removed test fails the job as unused, so a typo in the prefix
cannot pass unnoticed; each prints its own line saying what to fix.

**Intent pins:** `docs/intent/*.md` reference test ids by name. Renaming or
parametrizing a pinned test means repointing it in the same change. Run
`just intent` and `tests/test_intent_origins.py`. `just intent-repoint OLD NEW`
rewrites every pin on `OLD` (or `OLD[case]`) to `NEW` in one go, and refuses
when `NEW` does not collect.

## Running

- `just test <paths>` (testmon), never raw pytest. `just test-ui` for the
  frontend.
- Testmon follows Python execution only, so it cannot see a change to a
  file a test reads or runs as a subprocess: a harness YAML, a skill's
  `.md`, `fixtures/fake-claude.sh`, `tests/support/` (the sample repo, the
  fake agents), `config/`, `docs/intent/`. When one of those differs from
  the merge base with `origin/main` (committed on the branch or not),
  `just test <paths>` names the files and runs the paths with `--no-testmon`;
  a bare `just test` only warns, since its fallback would be the whole suite.
  `src/kraft/_bundled` is built from `config/` and `plugins/kraft/skills`,
  which are watched in its place. Environment variables are the same blind
  spot and are not watched.
- `just test <paths>` fails when nothing ran: a path that collects nothing,
  and a run testmon (or `-k`/`-m`) deselected to `0 selected`. Pass
  `--no-testmon` to run the paths regardless.
- The full suite runs in CI, so push and read CI rather than running it
  locally. CI runs the unit tier on every supported Python (3.12, 3.13, 3.14);
  `just test-py 3.12` reproduces one version's failure locally, in its own
  environment. A test must pass on the floor, so it never relies on a newer
  stdlib API or on 3.14's lazy annotations.
- CI runs the unit tier in three shards, split by test file. A failure that
  only shows beside the other tests of its shard is reproduced with the
  shard's name from the job, `test (python 3.14, 2/3)`:
  `KRAFT_TEST_SHARD=2/3 just test --no-testmon -n auto`. The repo-root
  `conftest.py` reads the variable; unset, every test runs.
- Only one heavy local test run at a time on a shared machine.
- A test that runs past its timeout (120s, `pyproject.toml`) ends its whole
  process: pytest-timeout's `thread` method dumps every stack, then exits.
  Under xdist that reads `[gwN] node down: Not properly terminated` and
  `worker 'gwN' crashed while running '<test>'`; `<test>` is the one that
  hung, and the `Timeout` stack dump above says where. Its teardown never
  runs, so what it started outside the process stays. The sandbox e2e
  fixture (`tests/worker/test_egress_docker.py`) removes the relays, volume
  and worker a killed run left at the next run. So a test's own wait must
  end well inside that timeout, or the test must raise its own with
  `@pytest.mark.timeout(N)`: a wait the timeout cuts short never gets to
  fail with its own message.

## Enforced mechanically

`dev/check_tests.py`, run in CI's `lint` job, next to
`dev/check_docs_coverage.py`. It checks what can be checked statically —
not "did you prove a mutation," which needs a human or an agent, but the
shape rules that don't — over both testpaths, `tests/` and
`plugins/kraft-lite/tests/`:

- **(a)** every `pytest.mark.e2e` names at least one CLI.
- **(b)** no test file outside the e2e tier launches a real `bd`, agent CLI
  or `gh`/`glab`: an argv list (positional or `args=`), a shell string, a
  `sh -c` or `env` wrapper, a variable or helper bound to such an argv,
  `asyncio.create_subprocess_exec`/`_shell`, `os.system`/`exec*`, and
  `run_git(repo, [...])`. Module-level code counts too. A file whose hits are
  on purpose (`tests/test_no_real_agent.py`, which probes the runtime guard)
  is in `REAL_CLI_ALLOWLIST` with its reason, and an entry that no longer
  has a hit fails the check. This is a static belt-and-braces check; the
  runtime guard (`tests/conftest.py`'s autouse fixtures) is what actually
  stops it at test time.
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
  or delegates to a same-module helper that does. One that cannot fail does
  not count: `assert True` (or `x or True`, or a tuple), a bare
  `pytest.raises(Exception)` with no `match=`, an assert under
  `if False:`, or one only inside a nested function nothing calls or passes
  on. A handful of tests where
  "did not raise" is genuinely the only honest assertion are named in
  `EXPECTATION_ALLOWLIST` (same file), each with an inline reason; an entry
  for a test id that no longer exists fails the check.
- **(e)** no helper body is copied across test files: a module-level function
  (not `test_*`) with the same body as one in another test file, or as a
  function in `tests/support/` (underscore or not), fails the check, naming
  both and, for a `tests/support` match, what to import. "The same body"
  ignores the docstring, annotations and keyword order, but not the values of
  the module constants the body names. Rule (c) is satisfied by splitting a
  file; this is what makes a split safe, since a split that copies its helpers
  into every half has only moved the bulk. The copies that existed when the
  rule landed are held by `DUPLICATE_HELPER_CEILING`, two numbers that may
  shrink but never grow: how many duplicated groups there are, and how many
  copies they hold. A change that pushes either past its ceiling fails, naming
  every group and its copies so the new one is easy to find. A ceiling that
  sits more than a small margin above the real count fails until it's
  tightened (`just shape-report --print-helper-ceiling` prints the real
  numbers).

A parse failure in the checker is a failure, not a skip — a checker that
can't read a file must not read as "passing" (this is the same bug class
the docsite coverage check guards against: silent success is worse than a
loud, wrong failure).

Run it directly: `uv run python dev/check_tests.py`, or `just check-tests`.

`just shape-report` (`dev/test_shape_report.py`) judges nothing, it measures
`tests/`: test functions and collected cases, lines, verbatim-repeat lines,
helpers copied across files, and the modules with the most tests per code line.
