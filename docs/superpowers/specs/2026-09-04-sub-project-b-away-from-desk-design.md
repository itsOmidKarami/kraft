# Sub-project B: away from desk

Date: 2026-09-04
Beads: Kraft-8mu.4 (parent Kraft-8mu); blocked by Kraft-8mu.2

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

`api.py:147` already fans every database commit out to two subscribers:

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

`styles.css` holds three `@media` queries in total; `nocturne.css` holds none.
The responsive work is real but bounded, because only two screens matter:

- **The board** — read which items need you.
- **The gate** — `Gate.tsx`, both variants, plus whatever `Kraft-8mu.2` adds to
  the `artifact` slot.

Everything a notification links to must work on a phone; nothing else has to.
Settings edits YAML, Analytics is a dense table, and the log viewer is a wall of
monospace — all three are desktop tasks, and making them phone-shaped is work
spent on screens nobody opens from a phone.

Two existing behaviours need attention rather than layout:

- `open-worktree` and `DocumentModal`'s editor-launch menu are local-only by
  construction. Both must be hidden, not merely broken, when the browser is not
  on the server's machine. The diff viewer from `Kraft-8mu.2` is what replaces
  the first of them remotely.
- The reject textarea in `Gate.tsx` is the one place a phone user types. It gets
  the layout attention.

## 5. Configuration and the secret

Notification config lives in a new `$KRAFT_HOME/templates/notify.yaml`, edited by
a Settings screen like every other file there:

```yaml
enabled: false
url: null
events: [gate_requested, work_item_needs_human]
```

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
- frontend: board and gate render usably at a phone viewport; local-only actions
  are absent when the server reports it cannot act on them

## Acceptance

- A gate opening on a machine the human is not at produces a message on their
  phone with a working link.
- The linked page allows approve and reject, with the diff, on that phone.
- Disabling notifications is one flag and produces no outbound traffic.
- `just test`, `just test-ui`, `just lint` pass.
