---
title: HTTP API
navigation:
  title: Overview
description: Which of Kraft's HTTP routes are stable, which the pages here document, where the full schema is, and who may call them.
---

The Kraft server serves its board, and the JSON API behind it, on one port
(`127.0.0.1:8765` by default). The `kraft` command and the MCP server are
clients of that API. Two routes are stable. The pages here document the work-item routes a script plausibly calls, and [the schema](#the-schema) lists every route: `/openapi.json`, with `/docs` and `/redoc` to read it.

A script that must outlive a release calls the two stable routes, `kraft … --json` or the MCP server, not the board's routes.

## Stable routes

Only two routes are meant for other programs, and only these keep their shape
between minor releases. The schema tags them `stable`. See
[Versioning and stability](/project/status-and-support#versioning-and-stability).
Who may call them is under [Who may call](#who-may-call).

| Route | Use |
|---|---|
| `POST /api/triggers` | File a [work item](/concepts/vocabulary#work-item) from a webhook or script. See [Inbound triggers](/reference/triggers#post-apitriggers). |
| `GET /api/health` | Check that the server is up. Needs no login. See [`GET /api/health`](#get-apihealth). |

### `GET /api/health`

```bash
curl -s http://127.0.0.1:8765/api/health | jq .status
```

The answer carries:

| Field | Meaning |
|---|---|
| `status` | `ok` or `degraded`. |
| `invalid_templates`, `invalid_policy`, `invalid_intake`, `invalid_forges` | The reasons it is degraded. `invalid_intake` is why `intake.yaml` doesn't load, else `null`; `invalid_forges` is the same for `forges.yaml`. |
| `forge_tokens` | Each `client: api` account's last token check, by name: `{state, expires_on}`, never who it signs in as. A `state` of `no_token`, `refused`, `forbidden` or `tls`, or an `expires_on` within 7 days, makes `status` `degraded`. _Added in 2.5_ |
| `storage` | `null` without `storage.worktrees.limit`, else `{state, used_bytes, quota_bytes, limit_bytes, measured_at, too_recent}`. `state` is `ok`, `over_quota` or `held`, and `held` makes `status` `degraded`. `too_recent` counts finished items the last automatic clean-up left because they ended less than `min_age` ago. |
| `intake_off` | Whether [auto-intake](/concepts/vocabulary#auto-intake) and `intake.yaml`'s schedules are off for it. |
| `mcp_command_on_path` | Whether Claude can start its user- or plugin-scope `kraft` MCP server on the server's `PATH`, which workers get. It leaves out a repo's own entries, which a launch also counts. `null`: nothing to check. _Added in 2.5_ |
| `addon_updates` | Each auto-updating add-on's last update, by add-on id: `{outcome, kind}`, without the message, which `kraft admin doctor` shows. Empty when none has run. See [Auto-update](/reference/configuration/addons#auto-update). |
| `run_dir`, `pid`, `uptime_s`, `bind`, `port` | Which instance this is. |
| `version` | The version it runs. |
| `installed` | The version installed on disk, which differs until a restart finishes an update. |
| `reattach_summary` | What startup found of agent sessions that were running: `scanned`, `adopted`, `resolved_from_file`, `unknown` and `resumed_work_items`. Each `adopted`, `resolved_from_file` and `unknown` entry is `<work item id>/<session id>`, since a session ID is unique only within its work item. |
| `index` | The search index: `last_scan_at`, `repos_scanned`, `documents`, and the state of `embeddings`. |
| `kraft_default` | `{signature, count}`: a short hash of the lines the `kraft-default` row of `kraft admin doctor` shows, and how many; `{signature: null, count: 0}` when none. The lines are on `GET /api/kraft-default`. _Added in next_ |
| `session_expiry_days` | How long a login lasts, which the login screen shows. |

An add-on that is installed and didn't load is under `invalid_templates`, as `add-on <id>` with the reason, and `status` is `degraded`. A held or failed entry of `addon_updates` leaves `status` as it is.

`intake_off` is `true` when the server started on an `intake.yaml` that doesn't load. A [trigger](/concepts/vocabulary#trigger) left in `policy.yaml` still fires. It is `false` when a reload refused the file and the running schedules are kept.

## Who may call

| Caller | What it needs |
|---|---|
| A process on the same machine, while Kraft is bound to loopback | Nothing, as long as it addresses the server as `127.0.0.1`, `localhost` or `[::1]`. |
| Anyone, once Kraft is bound off loopback | A browser session from the login page, or the MCP bearer token in `$KRAFT_HOME/run/mcp-token`, sent as `Authorization: Bearer <token>`. |
| A trigger sender, once Kraft is bound off loopback | For `POST /api/triggers` only, the trigger token in `$KRAFT_HOME/run/trigger-token`, sent the same way. |
| A remote caller while no password is set | Nothing works. Kraft answers `403`. |

The MCP bearer token is a full-access credential. See [Security](/project/security) for the whole model, and [Remote access](/guides/run/remote-access) to set a password.

### Refusals

| Status | When |
|---|---|
| `401` | The request has no valid credentials. |
| `403` | A remote caller, while no password is set. |
| `403` | The `Host` isn't an allowed name: `unexpected Host for a server bound to …`. See the rules below. |
| `403` | A cross-site write. The `/api/ws/events` WebSocket refuses the handshake the same way when its `Origin` is another site's. |

A `Host` isn't allowed when:

- Kraft is bound to loopback and the request isn't addressed to `127.0.0.1`, `localhost` or `[::1]`.
- Kraft is bound off loopback and the request comes from a browser (it sends `Sec-Fetch-Site`, `Origin`, `Referer` or an HTML `Accept`) whose `Host` is neither a loopback name nor in `allowed_hosts`. The CLI, MCP and `curl` need no entry there.

A browser opening a page (a `GET` outside `/api` with an HTML `Accept`) gets the `403` as a short HTML page instead. The page names the `Host` it sent and how to allow it. The WebSocket applies the same `Host` rules.

## The schema

FastAPI generates a schema of every HTTP route, including the ones these pages leave out:

| Path | What it serves |
|---|---|
| `/openapi.json` | The OpenAPI schema, as JSON. Its `info` names Kraft and the installed version. |
| `/docs` | Swagger UI for that schema. |
| `/redoc` | ReDoc for that schema. |

```bash
curl -s http://127.0.0.1:8765/openapi.json | jq '.paths | keys | length'
curl -s http://127.0.0.1:8765/openapi.json | jq '[.paths[][] | select(.tags | index("stable")) | .summary]'
```

These three paths need no login. Open `/docs` or `/redoc` in a browser to read the API there. Both pages load their scripts from a public CDN, jsdelivr, each pinned to one exact release that your browser refuses unless it matches the hash Kraft ships. Nothing else on either page comes from another site. On a machine without internet access, load `/openapi.json` into your own OpenAPI viewer.

### What the schema covers

_Added in next_

Every route has a summary, a description and a tag. A route with a typed body lists its fields. `GET /api/work-items` and `GET /api/work-items/{id}` also describe their answers. The schema names the stable fields and allows others, so a client should ignore keys it doesn't know. A route's tag is its group:

| Tag | Routes |
|---|---|
| `stable` | The two stable routes, which also carry their own group's tag. |
| `work items` | Filing, reading, changing, events, diff, documents and attachments. |
| `gates and run control` | Gates, pause, resume, skip, escalate, end, archive, retry and the routes a worker calls. |
| `review` | Review threads, their comments and submitting a review. |
| `forge reads` | An item's merge request, pipelines, jobs, job logs, merged requests and labels. |
| `worker sessions` | Session logs, sub-agents and permission asks. |
| `drafts` | The Templates and Settings editors' drafts and their ops, and an item's own draft. |
| `repos, forges and workers` | Repos, forge accounts and worker machines. |
| `chains` | Chain templates, the library and the config checks. |
| `harnesses` | Harness profiles and providers. |
| `settings` | Policy, theme, intake, access, notifications and update settings. |
| `search and analytics` | Search, the index, indexed documents, editors and analytics. |
| `auth` | Login, logout and the browser sessions. |
| `server` | Health, applying saved changes, updates and storage. |

The schema describes the answers of only the work item list and detail routes, which the other routes build by hand. [The work item object](/reference/http-api/work-item-object) is the full reference for the one a client reads. The `/api/ws/events` WebSocket isn't an HTTP route, so the schema omits it. [Events](/reference/events#ws-apiwsevents) describes it.

The drafts routes and their ops are the Templates and Settings editors' own protocol. Read their schema entries to see what an editor sends, but expect them to change in any release.

## Work item IDs in a path

_Added in 2.1_

The `{id}` segment of a route under `/api/work-items/` may be the start of a
work item ID instead of all of it. A start is 4 characters or more, from `0-9`
and `a-z`. Kraft makes it the whole ID before the route runs, so the answer
and everything the route does name the whole ID.

| What the segment is | Answer |
|---|---|
| An item's whole ID | That item, even when it is also the start of a longer ID. |
| The start of exactly one item's ID | That item. |
| The start of more than one | `409`, naming every ID it starts. |
| Anything else, a value under 4 characters or one with another character included | `404 unknown work item`, as for an ID that doesn't exist. |

A caller the [refusals](#refusals) above turn away gets that refusal first, so
it learns nothing about which IDs exist. A worker inside a network sandbox
reaches Kraft through its own channel, which still takes only its own item's
whole ID.

## Session IDs in a path

A session ID is unique only within its work item, so a session's routes are under its item, as `/api/work-items/{id}/sessions/{session}/...`. `{id}` may be the start of the item's ID, as above. `{session}` is always the whole session ID. A session of another item answers `404 unknown session`.

```bash
curl -s "http://127.0.0.1:8765/api/work-items/k3f9/sessions/$SESSION/log?format=jsonl&follow=1"
```

`log` serves the session's log as plain text, `?format=text` for the same without colour codes, or `?format=jsonl` for lines, with `&follow=1` to stream them until the session stops. The older `/api/worker-sessions/{session}/...` routes still answer for a session ID that exactly one session has, for a client built before session IDs were per item.

A worker's own session travels in the `X-Kraft-Session-Id` header as `<work item id>/<session id>`, from [`KRAFT_SESSION_ID`](/reference/configuration/environment-variables#kraft_session_id). Any other value answers `400`, apart from a bare 32-character ID from a session of an older Kraft.

## Status codes

_Added in next_

The routes answer with a small set of codes.

| Status | Meaning |
|---|---|
| `200`, `201`, `204` | Done. `201` follows a create, `204` a delete with nothing to say. |
| `202` | Accepted, and not yet done, as for a restart. |
| `400` | The request is malformed for what the route can read. |
| `401`, `403` | No valid credentials, or a caller the route refuses: see [Refusals](#refusals). A `403` also answers an agent's call to a person's action, such as approving a gate. |
| `404` | No such item, repo or session. A work item ID prefix that matches nothing answers `404` too. |
| `409` | The item's state forbids the action: it has ended, isn't waiting at that gate, or a walk is already running. The `detail` says which. |
| `422` | A field is refused. `detail` is one sentence naming it. |
| `429` | Too many failed logins, or too many probes at once. |
| `502` | A call to the forge failed, or filing an item failed after its checks passed. `detail` carries the reason. |
| `503` | A configuration file doesn't load, so Kraft refuses new work until it does. Approve, reject and retry also answer it while a restart is marked for when nothing is running. |

What each [work item status](/reference/statuses) allows is in the `actions` field of [the work item object](/reference/http-api/work-item-object#the-actions-field).

## In this section

- [Filing, reading and deciding work items](/reference/http-api/work-items): file an item, read the board and one item, approve or reject a gate, and retry a stopped item.
- [The work item object](/reference/http-api/work-item-object): the three shapes a work item answers in, and the fields that need an explanation.
- [Connecting a repo](/reference/http-api/connect-a-repo): list, probe, connect and disconnect a repo.
