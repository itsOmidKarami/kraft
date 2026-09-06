# CLI Sub-project B: Watching Work

> **Status:** design proposed 2026-09-06, not yet built. Depends on A.

## 1. Problem

The one thing a browser is genuinely worse at than a terminal is following a
running job. Today, watching a Kraft agent work means keeping a tab open on the
detail screen. There is no `tail -f`.

The server already streams: `GET /worker-sessions/{sid}/log?format=jsonl&follow=1`
is SSE that ends when the session stops, `GET /work-items/{wid}/events` is a
seq-ordered history, and `WS /ws/events` is the live bus the SPA drives its
board from. None of it is reachable without a browser.

## 2. Scope

Three verbs: `kraft logs`, `kraft events`, `kraft watch`. Three new `client.py`
functions, two of them streaming — which is the real work here, because
`client.py` has only ever done request/response.

Out of scope: filtering log lines by tool or level (the UI's filter chips), and
any log storage change. `grep` is downstream of a followable stream.

## 3. Verbs

**`kraft logs [ID] [-f] [--session SID] [-n N]`** — the worker session log.
With no `--session`, picks the most recent session of the work item, because
"show me what it is doing now" is the question being asked. `-f` follows until
the session stops, then exits 0 — a follow that hangs after the agent is gone is
worse than no follow. `-n` limits the backlog printed before following, default
50 lines and `0` for none.

**`kraft events [ID] [--after SEQ] [--type T]`** — the chain's own history:
node transitions, gate decisions, escalations. This is the "why is it stopped"
view, where `logs` is the "what is it saying" view. Bounded output, no follow;
the live version of this is `watch`.

**`kraft watch [--repo R]`** — a live board. Renders the same rows as
`kraft list`, redrawing on each `/ws/events` message, scoped to the cwd repo by
default like `list`. Ctrl-C exits 0.

## 4. Rendering

A JSONL log line is `{n, ...}` plus whatever the agent adapter wrote. `render.py`
gains `log_line(obj)`: a timestamp, a kind, and the text, with the raw JSON
available under `--json` (where `logs` emits one JSON object per line — NDJSON,
not a JSON array, because the stream has no end to close a bracket on).

`kraft watch` redraws with ANSI cursor-up over the previous row count. No curses,
no alternate screen: the point is that the last frame stays in the scrollback
when you Ctrl-C. Non-tty `watch` refuses with a message pointing at `events`,
since redrawing into a pipe produces garbage.

## 5. Streaming in client.py

Two new async generators, both `AsyncIterator[dict]`:

```python
async def stream_log(session_id, after_line=0) -> AsyncIterator[dict]
async def stream_events(after_seq=0) -> AsyncIterator[dict]
```

`stream_log` parses SSE off `httpx.AsyncClient.stream`; the server's terminal
`event: end` frame closes the iterator. `stream_events` holds the websocket.

**Not exposed as MCP tools.** An MCP tool returns a value; a generator has no
value to return, and an agent that wants history calls `get_work_item` or the
non-streaming `events`. `client.py` stops being a strict superset of the MCP
surface here, and that is fine — the parent design's rule is that neither door
holds *logic* the other lacks, not that every function is a tool.

## 6. The websocket auth gap

`WS /ws/events` authenticates by **session cookie only** (`api.py:1225-1231`),
while every HTTP route also accepts the MCP bearer token (`api.py:321-323`).
On a loopback bind `_requires_auth` is false and nothing is checked, so
`kraft watch` works. On a LAN bind with a password — the away-from-desk setup —
`kraft watch` gets closed with 1008 and no CLI-shaped way to recover.

`kraft logs -f` does not have this problem: SSE is plain HTTP and the bearer
already works.

**Decided (2026-09-06): fixed in this sub-project.** Accept `Authorization: Bearer` on the
websocket handshake, mirroring the HTTP middleware, and keep the cookie path for
browsers. Ten lines in `api.py`, and it removes an inconsistency that exists
today regardless of the CLI — the away-from-desk setup is exactly where a live
board in a terminal earns its keep, so shipping `watch` without this would ship
it broken in its best use case.

## 7. Testing

- `logs` without `-f` against a fixture log: lines rendered, `-n` respected.
- `logs -f` against a running fake session: streams, then exits when the session
  row flips to stopped. Marked `slow` (real uvicorn — SSE through
  `ASGITransport` is not the same code path).
- `events --type` filters; `--after` resumes.
- `watch` non-tty refuses; tty path tested by driving one redraw with a fake
  event and asserting the frame.
- WS auth: with a password set and a LAN bind, `watch` connects with the bearer
  (fails today, passes after §6).

## 8. Decisions

1. **Websocket bearer auth is fixed here** (§6), not filed separately.
2. **`kraft watch` is board-level only.** No `kraft watch ID`: `logs -f` already
   follows one item, and a second per-item live view would duplicate it with a
   thinner stream.
