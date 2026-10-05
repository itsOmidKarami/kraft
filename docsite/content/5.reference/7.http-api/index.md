---
title: HTTP API
description: Which of Kraft's HTTP routes are stable, where the full schema is, and who may call them.
---

The Kraft server serves its board, and the JSON API behind it, on one port
(`127.0.0.1:8765` by default). The `kraft` command and the MCP server are
clients of that API.

## Stable routes

Only two routes are meant for other programs, and only these keep their shape
between minor releases. See
[Versioning and stability](/project/status-and-support#versioning-and-stability).

| Route | Use |
|---|---|
| `POST /api/triggers` | File a work item from a webhook or script. See [Inbound triggers](/reference/triggers#post-apitriggers). |
| `GET /api/health` | Check that the server is up. Needs no login. See [`GET /api/health`](#get-apihealth). |

### `GET /api/health`

The answer carries:

| Field | Meaning |
|---|---|
| `status` | `ok` or `degraded`. |
| `invalid_templates`, `invalid_policy`, `invalid_intake` | The reasons it is degraded. `invalid_intake` is why `intake.yaml` does not load, else `null`. |
| `intake_off` | Whether auto-intake and `intake.yaml`'s schedules are off for it: `true` when the server started on that file, though a trigger left in `policy.yaml` still fires; `false` when a reload refused it and the running ones are kept. |
| `run_dir`, `pid`, `uptime_s`, `bind`, `port` | Which instance this is. |
| `version` | The version it runs. |
| `installed` | The version installed on disk, which differs until a restart finishes an update. |

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

## Who may call

| Caller | What it needs |
|---|---|
| A process on the same machine, while Kraft is bound to loopback | Nothing, as long as it addresses the server as `127.0.0.1`, `localhost` or `[::1]`. |
| Anyone, once Kraft is bound off loopback | A browser session from the login page, or the MCP bearer token in `$KRAFT_HOME/run/mcp-token`, sent as `Authorization: Bearer <token>`. |
| A trigger sender, once Kraft is bound off loopback | For `POST /api/triggers` only, the trigger token in `$KRAFT_HOME/run/trigger-token`, sent the same way. |
| A remote caller while no password is set | Nothing works. Kraft answers `403`. |

A request without valid credentials gets `401`.

A request whose `Host` is not
an allowed name gets `403` ("unexpected Host for a server bound to …"):

- On a
  loopback bind that is any request not addressed to `127.0.0.1`, `localhost` or
`[::1]`.
- Off loopback it is a browser request (one that sends `Sec-Fetch-Site`,
`Origin`, `Referer` or an HTML `Accept`) whose `Host` is neither a loopback
name nor in `allowed_hosts`; the CLI, MCP and `curl` need no entry there.

A
browser opening a page (a `GET` outside `/api` with an HTML `Accept`) gets the
`403` as a short HTML page instead, naming the `Host` it sent and how to allow
it.

A cross-site
write also gets `403`. The `/api/ws/events` WebSocket applies the same `Host`
rules, and refuses the handshake (`403`) when its `Origin` is another site's.

The MCP bearer token is
a full-access credential. See [Security](/project/security) for the whole
model, and [Remote access](/guides/run/remote-access) to set a password.

## Other routes

Routes written on the pages below without the `/api`
prefix are relative to it, except the three paths under
[The schema](#the-schema), which are at the server root. A bare `GET /artifact`
is shorthand for `GET /api/work-items/{id}/artifact`.

- [Work items](/reference/http-api/work-items): status, overrides, cancelling, retrying and a merge request closed on the forge.
- [Budget, dry run and events](/reference/http-api/budget-and-events): the daily total, the dry run, events paging and the run summary.
- [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server): connecting a repo, chain templates, harnesses, changes saved but not applied, the update check and editors.
- [Drafts](/reference/http-api/drafts): the routes behind the Templates and Settings editors' drafts, and the `result` they answer.
- [Ops for each draft area](/reference/http-api/draft-ops): the ops a draft takes, and a work item's own draft.
