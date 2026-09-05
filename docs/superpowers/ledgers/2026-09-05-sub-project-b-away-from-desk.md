# SDD ledger — Sub-project B: away from desk

Bead: `Kraft-8mu.4`. Plan: `docs/superpowers/plans/2026-09-05-sub-project-b-away-from-desk.md`.
Spec: `docs/superpowers/specs/2026-09-04-sub-project-b-away-from-desk-design.md` (amended 2026-09-05).

Every decision taken without stopping to ask is recorded here as
`Ruling: <what> — <why> — <cost if wrong>`. Deferred findings are beads, not
bullet points that rot in a document.

## Rulings — spec amendment pass

**Ruling: the phone media query is not new work — the diff viewer is.** — §4 was
written against a tree with three `@media` queries and no phone rule; the merged
tree has a full design-1n block at `max-width: 640px` covering nav, board,
sidebar, detail, 44px touch targets and a `.desktop-only`/`.phone-only` pair
`WorkItemDetail.tsx` already uses. The unbuilt part is `DiffModal`, which landed
with sub-project A after the spec was written and is desktop-shaped (820px
dialog, `white-space: pre`). — **Cost if wrong:** a phone screen the spec
promised is left broken; caught by looking at a 390px viewport, cheap to add.

**Ruling: no "can the server act locally" API field.** — §4 asks that local-only
actions be hidden when the browser is not on the server's machine. `open-worktree`
needs nothing (`api.openWorktree` survives in `api.ts` but no component calls it,
so there is no button to hide), and for `DocumentModal`'s editor menu a loopback
`window.location.hostname` already answers the question. — **Cost if wrong:** an
SSH port-forward shows an editor menu that cannot launch anything; it already
degrades to a `vscode://` URL, so the failure is a no-op button, not data loss.

**Ruling: keep the coalescing window even though the two event types are
disjoint at the source.** — `store.request_gate` emits only `gate_requested` and
`store.mark_needs_human` only `work_item_needs_human`; no caller emits both. But
`POST /gates/{gate}/reject` writes `reject_gate` and then `mark_needs_human` in
the same second when the reject loop is exhausted, on an item that was just
notified about. Four lines, one real trigger. — **Cost if wrong:** a duplicate
message, or a suppressed second message within 10s on the same work item.

**Ruling: `base_url` added to `notify.yaml`, absent from the spec.** — §3 requires
"a URL to the item" and the server cannot derive one: `access.yaml` holds a
*bind*, which is `0.0.0.0` in exactly the LAN configuration this feature exists
for, and `http://0.0.0.0:8765` is not a tappable link. Falls back to
`http://{bind}:{port}`, correct on loopback and honestly useless off it; the
Settings copy says so rather than pretending. Not a secret, returned in full. —
**Cost if wrong:** an operator who never sets it gets a dead link off-loopback —
the notification still tells them something stopped.

**Ruling: `notify.yaml` is neither bundled nor seeded.** — `cli.py:seed_home`
already deletes `access.yaml` from the staged copy for this exact reason: a
secret and a hostname that belong to one machine. A missing file reads as the
defaults; the first `PUT /notify` creates it. — **Cost if wrong:** nothing; the
loader's defaults are the file's contents.

**Ruling: no CSS test for the phone rules.** — jsdom has no viewport and does not
evaluate media queries. The testable half of "works on a phone" is the class
boundary (which controls sit inside `.desktop-only`), which is exactly what
`WorkItemDetail.test.tsx:196-249` already asserts for the diff button. Verified
by eye at 390×844 instead. — **Cost if wrong:** a CSS regression ships unnoticed;
same exposure the rest of `styles.css` already has.

## Deferred findings

See beads. Nothing is recorded only here.

## Plan review — 10 findings, all fixed before any code

Dispatched a read-only reviewer at the plan against the merged tree, on the
standing assumption that every sub-project's plan on this project has invented
CSS variables, class names, test fixtures or component signatures. It had six.
Independent parallel verification found a seventh the reviewer did not.

| # | Finding | Fix |
|---|---|---|
| 1 | `renderSettings(path)` invented; the helper is `renderAt(path)` (`Settings.test.tsx:52`) | renamed, plus a `getNotify` default added to the file's `beforeEach` |
| 2 | Three invented event types (`worker_session_running`, `worker_session_reattached`, `fix_round_completed`), four real ones missed (`work_item_retried`, `worker_session_created`, `findings_measured`, `session_reattached`) | list replaced with the verified 21, and the derivation grep written into the test as a comment so it cannot silently rot |
| 3 | `@pytest.mark.asyncio` — this repo has no `pytest-asyncio` at all; all 11 async tests would error | all 11 rewritten in the repo's own pattern (`tests/test_ws.py:43-62`): sync `def`, inner `async def scenario()`, `asyncio.run(scenario())` |
| 4 | `render(<DocumentModal …>)` without the router; the file's helper is `wrap(ui)` and the component renders a `<Link>` | switched to `wrap(...)` |
| 5 | **Specificity bug that would have silently defeated Task 5.** `.doc-modal-actions > .desktop-only { display: flex }` is (0,2,0); `.desktop-only { display: none }` is (0,1,0) and media queries add no specificity, so the editor buttons would stay visible at 390px and no test would catch it | rule scoped inside `@media (min-width: 641px)` |
| 6 | `data-danger` on a `.btn` styles nothing — the only rule is `.overflow-menu button[data-danger]` (`styles.css:159`) | attribute dropped |
| 7 | Unquoted `-k a or b`; `justfile:81` passes `{{ARGS}}` through unquoted, so pytest gets `or` as a filename | quoted |
| 8 | `httpx.BaseTransport` annotation; `AsyncClient` takes `AsyncBaseTransport` | annotation corrected |
| 9 | `_drain` returned on `not n._inflight`, which is true *before* the drain task is scheduled — vacuous, and in the disabled case nothing is ever in flight | now polls the cursor to the tail *and* waits out in-flight sends |
| 10 | Line-number drift in five prose references | corrected |

**Ruling: event toggles use the `.switch` pattern, not checkboxes.** — Found
independently of the review. The plan reached for `<input type="checkbox">` inside
`.radio`; this codebase contains no `type="checkbox"` anywhere, and `.radio`
(`nocturne.css:175-188`) hides its input to draw a *round* dot — a radio's
affordance, wrong for a multi-select. `.switch` (`styles.css:798-810`) is already
here, already means on/off, and `PluginsPage` (`Settings.tsx:472-480`) is the
pattern to copy. — **Cost if wrong:** a settings control that looks like it
enforces single-select; cosmetic, one class swap.

**Ruling: the reviewer's verified-clean list is taken at face value.** — It
carried file:line evidence for each of the ~30 CSS class names, the three
`Settings.tsx` helper signatures, `Database`'s async/sync split, the `work_items`
columns, `httpx`'s `transport=None`, and route ordering. Spot-checked the four
highest-risk claims (`pytest-asyncio` absence, `DocumentModal` props, the CSS
class inventory, `httpx.MockTransport` under `AsyncClient`) against the tree
directly and all four held. — **Cost if wrong:** a task fails at its own
red-test step, which is where it would surface anyway.

## Corrections found by the whole-branch review

**The `read_yaml` root-cause claim was half wrong.** Sub-project B widened
`config.read_yaml` to `except (OSError, ValueError, yaml.YAMLError)` and the
ledger recorded that as fixing the recurring `UnicodeDecodeError` defect for
"repos.yaml, access.yaml, policy.yaml and registry.yaml". Only repos, access and
notify actually route through `read_yaml`. `policy.yaml` is loaded by
`policy.load_policy`, which still catches the narrow `(OSError, yaml.YAMLError)`,
and `lifespan` catches only `PolicyError` around it — so a non-UTF-8 `policy.yaml`
still crashes startup. `registry.yaml` goes through `templates.load_registry`,
which has no guard at all, and `load_templates` catches only `yaml.YAMLError`.
The fix this branch made is correct and this branch adds no new narrow read path;
the claim about its reach was not. Filed as its own bead rather than widened here
— `read_yaml` was already on B's diff, those two loaders are not.

**Deliberately deferred to a bead, not fixed here:** `notification_failed` renders
in the timeline as a bare type string. `EventTimeline.tsx`'s `detailOf` has cases
for other events and none for this one, so the `status`/`host`/`error` payload
that was designed to make a dead webhook diagnosable is not shown. §3's letter is
met — the event is in the timeline — but not its purpose. Left alone because
`EventTimeline.tsx` is live merge surface for sub-project G in another session,
and a one-case edit there buys a conflict for a cosmetic gain.
