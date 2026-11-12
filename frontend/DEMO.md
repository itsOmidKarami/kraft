# The dashboard demo

Part of the web UI; [`README.md`](README.md) is the getting-started.

`demo/` runs this UI with no Kraft server: the UI contract's scenario
(`e2e/contract/mocks/`), answered in the page instead of by Playwright. `just
demo` serves it at <http://localhost:5174/demo/>, and `npm run build:demo`
writes `dist-demo/`. The docs site has one per version, beside its pages, the
way it has their docs: `main`'s at `/next/demo/` and a release's, built from
its own tag, at `/demo/` (docs/writing-docs.md, "Versions of the docs").

`demo/main.ts` builds the scenario and hands it to `mocks/mockApi.ts`, the same
answers the contract suite gets. Its work items run Kraft's shipped chains, as
a real server freezes them for an item: `mocks/shippedChains.json`, which `just
shapes` rewrites from a real server (`tests/api/test_demo_chain.py`), read by
`mocks/ngChain.ts`. Each item stands where a real one in its state stands, with
the sessions and events a real server writes on the way: `default`'s 19 nodes,
`quick-task`'s two, and `docs_only`, five of `default`'s nodes. Each board item is about something of its own
(`mocks/ngContent.ts`): its brief, the files it changes, which its diff, comparison and header serve as its
implementation commits each task of its own plan (a chain with no plan, `docs_only`, reports no task) and, where verification's fix loop repaired something, a fix of its own, its merge
request on its repo's forge, its documents, each written for its title, and its agents' logs and sub-agents, about its own files. A gate's document is indexed when a person or a reviewer approves the gate, at that moment: a pending gate's is
listed under its synthetic id and never searched, a chain revision that changed nothing has none, and a docs item has
no work brief. Each finished agent session's summary is indexed too. Search matches every word of its query over
those, as the index does. Its history is timed so it has run for its own time, and its agents spend and take time by the size of its change (`sizeOf`): the lines and files it changes, half for a docs item. The time a finished item took is its running time as the server counts it (`caps.running_time`): its sessions' wall time, a forge wait's left out and two that overlap counted once. The item page's samples and the long and many variants keep the shared data. `demo/shim.ts` puts them under the page:
`fetch` and the events socket for `/api/**`, the clock moved to the scenario's
moment, and the two `<a href="/api/...">` log links. An action there sticks
until the page reloads: the mock changes the item as the server would
(`mocks/transitions.ts`), and each event it appends goes out on the events
socket (`broadcast`), so the board moves as the item does. The board moves
on its own too: `MockOptions.motion` (`mocks/motion.ts`), which only the
demo sets, moves each board item whose status is `active` one step along
its chain every 8–15 seconds, through the same transitions. It runs each
node as the sample items show it ran, from the one recipe both read
(`mocks/nodeRecipe.ts`), one beat a tick: each task's session with what its
task reports, verification's test scopes, code review and findings, the
draft's merge request, and CI waits that poll. The first item it files fails
a round of tests, so a fix cycle runs. A gate is asked for, its reviewer
runs, and the item waits for the visitor to answer it. A waiting or
rate-limited item is active again once its retry time passes, and a spend
cap stops an item before its next agent task; raising it retries the item at the step it stopped in, unless a fix cycle has run since its last rejection or retry. An escalation turn, the sample's or one the visitor starts,
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

Six unit tests hold the demo's data to Kraft itself. `demo/apiShapes.test.ts`
asks the mock, with the demo's options (`demo/scenario.ts`), every route in
`tests/api/api_shapes.json`, the keys a real server answers with, and fails
on a key or route the mock leaves out. `just shapes` rewrites that table
after a response changes. `demo/sampleCoverage.test.ts` fails when no sample
item shows a display status or stop kind. `demo/coherence.test.ts` fails when
a session carries what its task cannot: only an agent's session has a harness,
a model, usage and a summary, and a builtin, subprocess or forge task's has its
command's output instead. On an item with content, only the implementer, a repair
and a code review have sub-agents, each asking what fits what the change does to
its file and all running inside four fifths of their session's time, and only the
implementer and a repair edit files: every other agent reads them. It also fails when an item strays
from its chain: a session on a task or node its chain lacks, a chain that is not
its template's, a node count, a spend or a finished item's node that does not
add up, events out of time order, or a session no event created. And it fails
on a stop the walk could not have written: a fix loop's judge before a repair
has run, a stop reason the server does not write for that kind, an automatic
escalation turn answering a stop outside the stuck set, or a session summary
outside its item's folder. It fails when two board items share a brief or a list of changed files, an
item shows a change for a plan task it has not finished or reports a task its own plan lacks, a docs item changes code, a patch's header miscounts its
lines or calls a new or deleted file changed, a file's counts or a written-out hunk do not fit how it changes, a review thread quotes lines its diff does not show or names code its lines lack, a rejection's note is not its review's summary and the threads open when it was sent, a merge request is not its own on its repo's forge or a forge task's log names another's at any tick of a walk, a diff shows verification's late file or fix commit before a repair of its has finished, a board row's plan progress is not the task last reported while the item runs its implementation (or shows one when it does not), a spend cap stops an item on a node with no agent task left to launch, a finished agent's run is as long as its time cap or an implementer's longer than any on record, a red round of tests fails on a test other than its own late file's, a document's id
repeats, its documents are not those of the gates approved and its finished agent sessions or are not indexed at the
approval, a documents row lacks a key a real row has, a document is served before its node wrote it, a brief's or a merge request's list of files is not the files its diff shows with their counts, a review brief's "N files" miscounts its rows or its "Read first" is not its largest change, a document's text is another kind's or another item's, Search ignores its query, filters, ranking or limit or reaches
past the board, two started items show the same running time, in the default sample two finished ones the same spend or time taken or an item that does not spend less than one on its chain that changed more lines, or one spends as much as the demo team's cap. It checks the
sample, its long and many variants, the seeds that add sessions and the board
after live motion has walked two items it filed to the end, then the sessions
the doors make. It fails when an event's `node_id` column is not the node its
payload names, an agent session's cost is not what its tokens cost at its
model's prices, or, in the live walk, a command's session ran another scope's command. Node by
node, the walked items' events must be the finished sample item's: those rows
compare the walk with the seeds, which share one recipe, so a beat the recipe
drops passes them. The literal rows pin the recipe itself: each node's events,
written out in the server's order with their polls, for the finished sample
item and for the walked items. Each item's frozen chain says what a task is; `mocks/taskKinds.ts` reads
it for the sample items that have one, the doors and live motion, and the
older sample items, which have none, say it by their model.
`demo/deadEnds.test.ts` fails when a control leads nowhere: Duplicate answers
with a real paused item, and every component the default chain extends is in
the Library its editor links to.
`demo/clock.test.ts` fails when an answer is dated after the demo's clock
(`mocks/clock.ts`, which the page shifts to at start): a worker last seen, a
check, a document indexed or an event later than now, bar the keys that name a
moment still to come (a retry, an expiry). A past-tense fixture takes its stamp
from `ago`.
`demo/areas.test.ts` fails when Analytics, Harnesses or Workers says what the
board does not: Analytics is the board's items through the server's arithmetic
(`mocks/ngAnalytics.ts`), so a node, gate or stop the default chain lacks, a
repo the board lacks, a total, status count or a node's or repo's cost, tokens
or time that is not a sum over the items' sessions, a gate waited at more often
than it was asked, a median lead time none of them took, a repo filter or a
shorter range that counts what it should not, repairs that are not their fix
cycles, or a week after the clock fails it, and so does a long, many or empty
variant served anything but its own analytics; so does a harness lane task the
shipped chains lack, or a machine running more or fewer sessions than the board's.
