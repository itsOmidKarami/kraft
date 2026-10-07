---
title: HTTP API
navigation:
  title: Overview
description: Which of Kraft's HTTP routes are stable, where the full schema is, and who may call them.
---

The Kraft server serves its board, and the JSON API behind it, on one port
(`127.0.0.1:8765` by default). The `kraft` command and the MCP server are
clients of that API.

## Stable routes

Only two routes are meant for other programs, and only these keep their shape
between minor releases. See
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
| `invalid_templates`, `invalid_policy`, `invalid_intake` | The reasons it is degraded. `invalid_intake` is why `intake.yaml` does not load, else `null`. |
| `intake_off` | Whether [auto-intake](/concepts/vocabulary#auto-intake) and `intake.yaml`'s schedules are off for it. |
| `run_dir`, `pid`, `uptime_s`, `bind`, `port` | Which instance this is. |
| `version` | The version it runs. |
| `installed` | The version installed on disk, which differs until a restart finishes an update. |
| `reattach_summary` | What startup found of agent sessions that were running: `scanned`, `adopted`, `resolved_from_file`, `unknown` and `resumed_work_items`. |
| `index` | The search index: `last_scan_at`, `repos_scanned`, `documents`, and the state of `embeddings`. |
| `session_expiry_days` | How long a login lasts, which the login screen shows. |

`intake_off` is `true` when the server started on an `intake.yaml` that does not load. A [trigger](/concepts/vocabulary#trigger) left in `policy.yaml` still fires. It is `false` when a reload refused the file and the running schedules are kept.

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
| `403` | The `Host` is not an allowed name: `unexpected Host for a server bound to …`. See the rules below. |
| `403` | A cross-site write. The `/api/ws/events` WebSocket refuses the handshake the same way when its `Origin` is another site's. |

A `Host` is not allowed when:

- Kraft is bound to loopback and the request is not addressed to `127.0.0.1`, `localhost` or `[::1]`.
- Kraft is bound off loopback and the request comes from a browser (it sends `Sec-Fetch-Site`, `Origin`, `Referer` or an HTML `Accept`) whose `Host` is neither a loopback name nor in `allowed_hosts`. The CLI, MCP and `curl` need no entry there.

A browser opening a page (a `GET` outside `/api` with an HTML `Accept`) gets the `403` as a short HTML page instead. The page names the `Host` it sent and how to allow it. The WebSocket applies the same `Host` rules.

## The schema

FastAPI generates a schema of every route:

| Path | What it serves |
|---|---|
| `/openapi.json` | The OpenAPI schema, as JSON. Its `info` names Kraft and the installed version. |
| `/docs` | Swagger UI for that schema. |
| `/redoc` | ReDoc for that schema. |

```bash
curl -s http://127.0.0.1:8765/openapi.json | jq '.paths | keys'
```

These three paths need no login. Open `/docs` or `/redoc` in a browser to read
the API there.

Both pages load their scripts from a public CDN, jsdelivr. Each
script is pinned to one exact release, and your browser refuses it unless it
matches the hash Kraft ships. Nothing else on either page comes from another
site: their policy lets an image load only from Kraft itself or inline, so
ReDoc's "Redocly" logo is not fetched.

On a machine without internet access,
load `/openapi.json` into your own OpenAPI viewer.

## In this section

The pages below describe the board's routes. They can change in any release, so a script that must keep working calls the [stable routes](#stable-routes), or the `kraft` command with `--json`. Each route is written in full, with its `/api` prefix.

- [Work items](/reference/http-api/work-items): creating a work item, its status and `stop` fields, overrides, cancelling, retrying, duplicating, acting in bulk, and a merge request closed on the forge.
- [Budget, dry run and events](/reference/http-api/budget-and-events): the daily total, the dry run of a create, events paging, the run summary, and the other fields of a work item's detail.
- [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server): connecting a repo, chain templates, harnesses, changes saved but not applied, the update check and editors.
- [Drafts](/reference/http-api/drafts): the routes behind the Templates and Settings editors' drafts, and the `result` they answer.
- [Ops for each draft area](/reference/http-api/draft-ops): the ops a draft takes in each area, and a work item's own draft.
