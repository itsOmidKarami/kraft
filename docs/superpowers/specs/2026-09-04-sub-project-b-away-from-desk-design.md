# Sub-project B: away from desk

Date: 2026-09-04
Beads: Kraft-8mu.4 (parent Kraft-8mu); blocked by Kraft-8mu.2

**Amended 2026-09-05**, before implementation, against the merged tree. Sub-projects
D, A, F and C have all landed since this was written. Every amendment is marked
`[amended]`; the rest of the document stands as approved.

## Problem

Kraft's central promise is that a run stops and waits for a human. Nothing tells
the human it stopped. `grep -rniE "notify|webhook|slack" src/kraft` returns only
the WebSocket broadcaster's internal `notify()`. A gate can sit pending for a
day, and the only way to find out is to have the board open.

That is tolerable when Kraft runs on the machine in front of you and fatal for
the stated target — local install, reachable remotely, single user who is
sometimes elsewhere. A `needs_human` status nobody is told about is
indistinguishable from a hang.

Notifications and a phone-usable UI are one sub-project because either alone is
unusable: a notification you cannot act on, or a mobile screen you never know to
open.

**Blocked by `Kraft-8mu.2`** so that the diff viewer is made responsive once,
during its own implementation, instead of desktop-first and then retrofitted.

## Non-goals

- Email, and the SMTP configuration it drags in.
- A native mobile app. The notification carries a URL; the browser is the app.
- Inbound control — replying to a notification to approve a gate. Approving is a
  tap on a link away, and an inbound channel is an unauthenticated write path
  into an orchestrator that edits repositories.
- Making every screen phone-shaped. Settings, Analytics, and the log viewer stay
  desktop-only; §4.
- Multi-user routing. One instance, one human.

## 1. Where it hooks in

`api.py:156` already fans every database commit out to two subscribers:

```python
database.set_on_commit(lambda: (broadcaster.notify(), indexer.notify()))
```

The notifier is a third. It follows the broadcaster's existing pattern — wake on
commit, read `events` rows past its last seen `seq`, act, advance. Events are
generic `{seq, work_item_id, type, payload, created_at}` rows, so the trigger set
is a filter over `type` and needs no new instrumentation anywhere in the
executor.

Last-seen `seq` is held in memory and initialized to the current maximum at
startup. A restart therefore drops notifications for events that occurred while
the process was down. That is the right trade: the alternative is a persisted
cursor that fires a burst of stale "a gate is waiting" messages for gates that
were handled hours ago, and a notification the human has already acted on is
worse than one they missed, because it trains them to ignore the channel.

## 2. What notifies

Two types, and the restraint is the feature:

| Event type | Message |
|---|---|
| `gate_requested` | a decision is waiting |
| `work_item_needs_human` | stopped — gate wait or cap breach |

Nothing else. Not `node_started`, not `work_item_completed`, not
`node_completed`. A notifier that fires on progress gets muted within a week,
and a muted channel is identical to the channel not existing — which is the
problem this sub-project is solving. The two chosen types are exactly the states
where Kraft is blocked on the human and will make no further progress.

`work_item_needs_human` covers both a scheduled gate wait and a cap breach, per
the glossary. Where both it and `gate_requested` fire for the same transition,
the notifier sends one message, keyed on `work_item_id` within a short window.

**[amended]** In the merged tree the two types are disjoint at the source:
`store.request_gate` emits only `gate_requested`, `store.mark_needs_human` only
`work_item_needs_human`, and no caller invokes both. The coalescing window is
kept anyway — `POST /gates/{gate}/reject` writes `reject_gate` and then, when
the reject loop is exhausted, `mark_needs_human` in the same second, on an item
that had just been notified about. One window, one message, keyed on
`work_item_id`.

**Corrected after implementation:** the sentence above originally claimed the
test asserts that reject-exhaustion path rather than a synthetic double-append.
It does not, and it should not. Reaching that path needs a human to reject a
gate within `COALESCE_SECONDS` (10s) of the gate opening, which does not happen
— so the "real" test would be a test for an unreachable sequence. The shipped
test appends the two events directly, which is the honest way to exercise a
window whose only observable behaviour is the coalescing itself.

The event list is configurable, but the shipped default is these two.

## 3. Delivery

**One channel: an outbound HTTP POST to a user-supplied URL.**

A webhook is the only channel that reaches a human who is not at the machine,
and one URL covers ntfy, Pushover, Slack, Discord, and a self-written receiver
without Kraft integrating with any of them. An OS notification was considered
and rejected as the primary channel for the reason in the problem statement: it
appears on the machine the human is, by assumption, not at.

Payload is a small flat JSON object — `work_item_id`, `title`, `type`, `gate`,
and a URL to the item — chosen so it is readable as-is by a generic receiver.

Failure handling: one retry after a short delay, then give up and write a
`notification_failed` event against the work item. The event matters. This
channel exists to report that something stopped; if the reporter itself stops,
silently dropping the message reproduces the original bug one level up, and the
event makes the failure visible in the timeline the human eventually opens.

Delivery is fire-and-forget on the event loop with a hard timeout. A slow or
hanging webhook endpoint must not stall the commit fan-out that the WebSocket
and the indexer share.

## 4. The phone UI

**[amended]** This section was written against a tree where `styles.css` held
three `@media` queries and no phone rule. It now holds a real design-1n phone
block at `max-width: 640px` covering the nav, the board grid and sidebar, the
detail screen, 44px touch targets on gate and board buttons, and a
`.desktop-only` / `.phone-only` pair that `WorkItemDetail.tsx` already uses to
hide steer/retry/pause off-desktop and show "Open on desktop to steer or retry".
The board and the detail shell are therefore **done**. What is *not* done is the
one thing this sub-project was blocked on `Kraft-8mu.2` for, plus one control:

- **The diff viewer** — `DiffModal.tsx` / `.diff-modal`. Landed with `Kraft-8mu.2`
  after this spec was written, and it is desktop-shaped: an 820px dialog whose
  `.diff-body > div` is `white-space: pre`, inside a `.dialog-backdrop` the phone
  block only pads. It is reached from the gate's `artifact` slot for
  `human_review_approval` — i.e. it is on the exact path a notification links to.
- **The reject textarea** in `Gate.tsx`, per the bullet below.

The board and gate needed no further work beyond confirming both at a phone
viewport; the responsive budget goes to the diff viewer instead.

Everything a notification links to must work on a phone; nothing else has to.
Settings edits YAML, Analytics is a dense table, and the log viewer is a wall of
monospace — all three are desktop tasks, and making them phone-shaped is work
spent on screens nobody opens from a phone.

Two existing behaviours need attention rather than layout:

- `open-worktree` and `DocumentModal`'s editor-launch menu are local-only by
  construction. Both must be hidden, not merely broken, when the browser is not
  on the server's machine. The diff viewer from `Kraft-8mu.2` is what replaces
  the first of them remotely.

  **[amended]** `open-worktree` needs nothing: `api.openWorktree` survives in
  `api.ts` but no component calls it, so there is no button to hide. Its
  replacement, "Review changes" → `DiffModal`, is already unconditional in
  `WorkItemDetail.tsx`. `DocumentModal`'s editor menu is not broken either — it
  already falls back to a `vscode://file/...` URL the *viewer's* machine
  honours. That fallback is meaningless on a phone, so the menu is hidden by the
  existing `.desktop-only` class and "Copy path" stays. No new server capability
  flag: a browser on a loopback origin is on the server's machine, which is a
  `window.location.hostname` test, not an API field.
- The reject textarea in `Gate.tsx` is the one place a phone user types. It gets
  the layout attention.

## 5. Configuration and the secret

Notification config lives in a new `$KRAFT_HOME/templates/notify.yaml`, edited by
a Settings screen like every other file there:

```yaml
enabled: false
url: null
base_url: null
events: [gate_requested, work_item_needs_human]
```

**[amended]** `base_url` was missing from the original and the payload cannot be
built without it. §3 requires "a URL to the item", and the server does not know
its own reachable address: `access.yaml` holds a *bind*, which is `0.0.0.0` in
exactly the LAN configuration this feature exists for, and `http://0.0.0.0:8765`
is not a link anyone can tap. `base_url` is the operator's answer to "what do I
type into my phone" — a LAN IP or a tunnel hostname. Unset, it falls back to
`http://{bind}:{port}`, which is correct on loopback and honestly useless off it;
the Settings screen says so rather than pretending. It is not a secret and is
returned by `GET /notify` in full.

`notify.yaml` is not bundled and not seeded, for `access.yaml`'s reason
(`cli.py:seed_home` deletes that one from the staged copy): it holds a secret and
a hostname that belong to one machine. A missing file reads as the defaults
above, and the first `PUT /notify` creates it. `templates.CONFIG_FILES` gains
`notify.yaml` so `load_templates` does not read it as a malformed chain template
and degrade `/health`.

Separate from `access.yaml` because that file governs how Kraft is reached and
this governs how Kraft reaches out.

**The webhook URL is the first true secret Kraft stores.** `access.yaml` holds a
password *hash*, which is safe at rest; a webhook URL usually embeds a token in
the path or query and cannot be hashed, because Kraft has to send it.

Consequences, all of which the implementation must honour:

- `notify.yaml` is written `0600`.
- `GET /notify` returns `url_set: true|false`, never the URL. A settings screen
  that renders the value back into the DOM puts the token in the browser, in
  screenshots, and in any future session recording.
- `PUT /notify` accepts a URL to set, and a sentinel to clear it. Omitting the
  field leaves the stored value alone, so editing the event list does not
  require re-entering the secret.
- The URL is never written to a log or an event payload. `notification_failed`
  records a status code and a host, not the URL.

## 6. Testing

- notifier fires on `gate_requested` and `work_item_needs_human`, and on nothing
  else, including a full pass over every emitted type
- the two events for one transition coalesce into one send
- startup does not replay events older than the process
- a failing endpoint retries once, then writes `notification_failed`
- a hanging endpoint does not delay the WS broadcaster — asserted against the
  existing WS test harness
- `GET /notify` never returns the URL; a `PUT` omitting `url` preserves it
- the URL appears in no log file or event payload after a failed send
- frontend: the diff viewer renders usably at a phone viewport, and the reject
  textarea in `Gate.tsx` is reachable and typable there
- frontend: `DocumentModal`'s editor-launch menu is inside a `.desktop-only`
  wrapper while "Copy path" is not

**[amended]** "board and gate render usably at a phone viewport" is dropped as a
new test: `WorkItemDetail.test.tsx` already asserts the `.desktop-only` boundary
for the controls, and jsdom has no viewport to assert a media query against —
the existing class-boundary assertions are the testable form of that claim, and
the diff viewer gets the same treatment rather than a new kind of test.

## Acceptance

- A gate opening on a machine the human is not at produces a message on their
  phone with a working link.
- The linked page allows approve and reject, with the diff, on that phone.
- Disabling notifications is one flag and produces no outbound traffic.
- `just test`, `just test-ui`, `just lint` pass.
