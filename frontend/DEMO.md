# The dashboard demo

Part of the web UI; [`README.md`](README.md) is the getting-started.

`demo/` runs this UI with no Kraft server: the UI contract's scenario
(`e2e/contract/mocks/`), answered in the page instead of by Playwright. `just
demo` serves it at <http://localhost:5174/demo/>, and `npm run build:demo`
writes `dist-demo/`. The docs site has one per version, beside its pages, the
way it has their docs: `main`'s at `/next/demo/` and a release's, built from
its own tag, at `/demo/` (docs/writing-docs.md, "Versions of the docs").

`demo/main.ts` builds the scenario and hands it to `mocks/mockApi.ts`, the same
answers the contract suite gets. `demo/shim.ts` puts them under the page:
`fetch` and the events socket for `/api/**`, the clock moved to the scenario's
moment, and the two `<a href="/api/...">` log links. An action there sticks
until the page reloads: the mock changes the item as the server would
(`mocks/transitions.ts`), and each event it appends goes out on the events
socket (`broadcast`), so the board moves as the item does. The board moves
on its own too: `MockOptions.motion` (`mocks/motion.ts`), which only the
demo sets, moves each board item whose status is `active` one step along
its chain every 8–15 seconds, through the same transitions. A node takes
two ticks, with a session for each task it runs, and a gate stops the item
until the visitor answers it. A waiting or rate-limited item is active
again once its retry time passes, and a spend cap stops an item before its
next agent task. An escalation turn, the sample's or one the visitor starts,
ends on its own two ticks in, as its agent finishing does: its session is done,
its reply is the item's document (the task's Output links it), and the item
stays as it stopped, for a reply, a retry or a decision. A finished chain is Done. When no board item is active
and none waits at a gate, it files a fresh item, so a gate always comes
round again. `whileShown` pauses it while the tab is hidden. A reload starts
from the sample data again. The routers take the build's base
(`import.meta.env.BASE_URL`), `/` everywhere but here. A change to what the
demo shows is a change to the contract's mocks, so it is checked there.

The demo adds one thing to the page, outside the React tree and drawn in the
app's tokens: `demo/badge.ts`, the corner badge, which opens a list of five
things to try. Nothing for it lives in `src/`. Its × hides the badge for the
visit. The list opens by itself once, on a desktop's first visit, and after
that stays as the visitor left it (`localStorage`). Phones start with it shut.

- `demo/tries.ts` holds the five entries. Each is ticked by the request that
  does it: `shim.ts`'s `watch` hands every answered request to `tickOf`. Each
  has a Show me: a real link to where it can be done now, such as whichever
  item waits at a gate. The page is found by reading the mock directly, so the
  look itself ticks nothing. It is read again when the list opens, after a
  request that may move things, and on each event the mock appends, live
  motion's too. Those events tick nothing: motion is not the visitor.
- A Show me's click goes by `pushState` and a `popstate`, so the demo is not
  reloaded. A click already handled is left alone, and so is a click asking
  for a new tab. One such click is the item page's leave guard, for an
  unapplied chain draft: its dialog then goes on to the page with no ring.
- `demo/ring.ts` then finds the target by role and accessible name, as
  Playwright does, scrolls it into view, and rings it. The ring hides while
  the target is scrolled away. On the phone layout, which draws no such
  control, there is no ring.

`e2e/demo/` is the demo's smoke suite: the built demo on `vite preview`, booted,
and every Show me followed to its target on the desktop layout. A rename that
strands an entry fails it. It keeps live motion still, as a hidden tab does
(the tab reads hidden while the demo boots), so each row meets the sample
data. `just demo-smoke` runs it (`npm run demo:smoke` without `just`), and so
does CI's `ui contract` job.

Three unit tests hold the demo's data to Kraft itself. `demo/apiShapes.test.ts`
asks the mock, with the demo's options (`demo/scenario.ts`), every route in
`tests/api/api_shapes.json`, the keys a real server answers with, and fails
on a key or route the mock leaves out. `just shapes` rewrites that table
after a response changes. `demo/sampleCoverage.test.ts` fails when no sample
item shows a display status or stop kind. `demo/coherence.test.ts` fails when
a session carries what its task cannot: only an agent's session has a harness,
a model, usage, a summary and sub-agents, and a builtin, subprocess or forge
task's has its command's output instead. It checks the sample, its long
variant and the seeds that add sessions, then the sessions the doors make. Each item's frozen chain says what a task is; `mocks/taskKinds.ts` reads
it for the sample items that have one, the doors and live motion, and the
older sample items, which have none, say it by their model.
