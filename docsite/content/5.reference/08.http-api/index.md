---
title: HTTP API
navigation:
  title: Overview
description: Which of Kraft's HTTP routes are stable, which the pages here document, where the full schema is, and who may call them.
---

The Kraft server serves its board, and the JSON API behind it, on one port
(`127.0.0.1:8765` by default). The `kraft` command and the MCP server are
clients of that API. The pages in this section document part of it: see [Routes the pages document](#routes-the-pages-document). [`/openapi.json`](#the-schema) lists every HTTP route.

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

FastAPI generates a schema of every HTTP route:

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

## Routes the pages document

The pages in this section document the board's routes for work items, repos, chains, harnesses, settings, drafts and the server's own state, and the `/api/ws/events` WebSocket. They do not document every route. [`/openapi.json`](#the-schema) lists every HTTP route, but not the WebSocket, which is only described under [Events](/reference/events#ws-apiwsevents). The pages leave out these groups, which the board uses and a script rarely needs:

- review threads and their comments;
- login and logout, and the browser sessions;
- search, the index rescan, and reading one indexed document;
- worker session logs and permission asks;
- analytics;
- reading and saving the theme, access, notify and intake settings, saving the library, reading and saving a chain's file, and the checks that run on them;
- the Settings views of one harness profile or provider.

A route listed here is written in full: its method and its path with the `/api` prefix, as in `POST /api/work-items`. That is how the route appears in `/openapi.json` and in a request. A route that is not in the table has no entry.

The pages can change in any release. A script that must keep working calls the [stable routes](#stable-routes), or the `kraft` command with `--json`.

| Acts on | Route | Page |
|---|---|---|
| Stable | [`POST /api/triggers`](/reference/triggers#post-apitriggers) | [Inbound triggers](/reference/triggers) |
| Stable | [`GET /api/health`](#get-apihealth) | This page |
| Work item | [`POST /api/work-items`](/reference/http-api/work-items#post-apiwork-items) | [Work items](/reference/http-api/work-items) |
| Work item | [`POST /api/work-items/{id}/duplicate`](/reference/http-api/work-items#post-apiwork-itemsidduplicate) | [Work items](/reference/http-api/work-items) |
| Work item | [`GET /api/work-items`](/reference/http-api/work-items#get-apiwork-items) | [Work items](/reference/http-api/work-items) |
| Work item | [`GET /api/work-items/{id}`](/reference/http-api/work-items#get-apiwork-itemsid) | [Work items](/reference/http-api/work-items) |
| Work item | [`PATCH /api/work-items/{id}`](/reference/http-api/work-items#patch-apiwork-itemsid) | [Work items](/reference/http-api/work-items) |
| Work item | [`POST /api/work-items/bulk`](/reference/http-api/work-items#post-apiwork-itemsbulk) | [Work items](/reference/http-api/work-items) |
| Work item | [`POST /api/work-items?dry_run=1`](/reference/http-api/budget-and-events#post-apiwork-itemsdry_run1) | [Budget, events, diff and documents](/reference/http-api/budget-and-events) |
| Gate | [`POST /api/work-items/{id}/gates/{gate}/approve`](/reference/http-api/work-items#post-apiwork-itemsidgatesgateapprove) | [Work items](/reference/http-api/work-items) |
| Gate | [`POST /api/work-items/{id}/gates/{gate}/reject`](/reference/http-api/work-items#post-apiwork-itemsidgatesgatereject) | [Work items](/reference/http-api/work-items) |
| Run control | [`POST /api/work-items/{id}/pause`](/reference/http-api/work-items#post-apiwork-itemsidpause) | [Work items](/reference/http-api/work-items) |
| Run control | [`POST /api/work-items/{id}/steer`](/reference/http-api/work-items#post-apiwork-itemsidsteer) | [Work items](/reference/http-api/work-items) |
| Run control | [`POST /api/work-items/{id}/resume`](/reference/http-api/work-items#post-apiwork-itemsidresume) | [Work items](/reference/http-api/work-items) |
| Run control | [`POST /api/work-items/{id}/skip`](/reference/http-api/work-items#post-apiwork-itemsidskip) | [Work items](/reference/http-api/work-items) |
| Run control | [`POST /api/work-items/{id}/escalate`](/reference/http-api/work-items#post-apiwork-itemsidescalate) | [Work items](/reference/http-api/work-items) |
| Run control | [`POST /api/work-items/{id}/escalate/stop`](/reference/http-api/work-items#post-apiwork-itemsidescalatestop) | [Work items](/reference/http-api/work-items) |
| Run control | [`POST /api/work-items/{id}/retry`](/reference/http-api/work-items#post-apiwork-itemsidretry) | [Work items](/reference/http-api/work-items) |
| Run control | [`POST /api/work-items/{id}/reopen-mr`](/reference/http-api/work-items#post-apiwork-itemsidreopen-mr) | [Work items](/reference/http-api/work-items) |
| Run control | [`POST /api/work-items/{id}/progress`](/reference/http-api/work-items#post-apiwork-itemsidprogress) | [Work items](/reference/http-api/work-items) |
| Run control | [`POST /api/work-items/{id}/mr-labels`](/reference/http-api/work-items#post-apiwork-itemsidmr-labels) | [Work items](/reference/http-api/work-items) |
| Ending | [`GET /api/work-items/{id}/cancel-preview`](/reference/http-api/work-items#get-apiwork-itemsidcancel-preview) | [Work items](/reference/http-api/work-items) |
| Ending | [`POST /api/work-items/{id}/cancel`](/reference/http-api/work-items#post-apiwork-itemsidcancel) | [Work items](/reference/http-api/work-items) |
| Ending | [`POST /api/work-items/{id}/complete`](/reference/http-api/work-items#post-apiwork-itemsidcomplete) | [Work items](/reference/http-api/work-items) |
| Ending | [`POST /api/work-items/{id}/archive`](/reference/http-api/work-items#post-apiwork-itemsidarchive) | [Work items](/reference/http-api/work-items) |
| Ending | [`POST /api/work-items/{id}/restore`](/reference/http-api/work-items#post-apiwork-itemsidrestore) | [Work items](/reference/http-api/work-items) |
| Ending | [`POST /api/work-items/{id}/abandon`](/reference/http-api/work-items#post-apiwork-itemsidabandon) | [Work items](/reference/http-api/work-items) |
| Budget | [`GET /api/budget/today`](/reference/http-api/budget-and-events#get-apibudgettoday) | [Budget, events, diff and documents](/reference/http-api/budget-and-events) |
| Budget | [`POST /api/work-items/{id}/budget/raise`](/reference/http-api/budget-and-events#post-apiwork-itemsidbudgetraise) | [Budget, events, diff and documents](/reference/http-api/budget-and-events) |
| Events | [`GET /api/work-items/{id}/events`](/reference/http-api/budget-and-events#get-apiwork-itemsidevents) | [Budget, events, diff and documents](/reference/http-api/budget-and-events) |
| Events | [`WS /api/ws/events`](/reference/events#ws-apiwsevents) | [Events](/reference/events) |
| Diff and review | [`GET /api/work-items/{id}/diff`](/reference/http-api/budget-and-events#get-apiwork-itemsiddiff) | [Budget, events, diff and documents](/reference/http-api/budget-and-events) |
| Diff and review | [`GET /api/work-items/{id}/compare`](/reference/http-api/budget-and-events#get-apiwork-itemsidcompare) | [Budget, events, diff and documents](/reference/http-api/budget-and-events) |
| Diff and review | [`PUT /api/work-items/{id}/viewed`](/reference/http-api/budget-and-events#put-apiwork-itemsidviewed) | [Budget, events, diff and documents](/reference/http-api/budget-and-events) |
| Diff and review | [`DELETE /api/work-items/{id}/viewed`](/reference/http-api/budget-and-events#delete-apiwork-itemsidviewed) | [Budget, events, diff and documents](/reference/http-api/budget-and-events) |
| Diff and review | [`GET /api/work-items/{id}/fix-target`](/reference/http-api/budget-and-events#get-apiwork-itemsidfix-target) | [Budget, events, diff and documents](/reference/http-api/budget-and-events) |
| Diff and review | [`POST /api/work-items/{id}/review`](/reference/http-api/budget-and-events#post-apiwork-itemsidreview) | [Budget, events, diff and documents](/reference/http-api/budget-and-events) |
| Documents | [`GET /api/work-items/{id}/attachments/{kind}`](/reference/http-api/budget-and-events#get-apiwork-itemsidattachmentskind) | [Budget, events, diff and documents](/reference/http-api/budget-and-events) |
| Documents | [`GET /api/work-items/{id}/documents`](/reference/http-api/budget-and-events#get-apiwork-itemsiddocuments) | [Budget, events, diff and documents](/reference/http-api/budget-and-events) |
| Documents | [`GET /api/work-items/{id}/artifact`](/reference/http-api/budget-and-events#get-apiwork-itemsidartifact) | [Budget, events, diff and documents](/reference/http-api/budget-and-events) |
| Documents | [`GET /api/work-items/{id}/artifacts/{kind}`](/reference/http-api/budget-and-events#get-apiwork-itemsidartifactskind) | [Budget, events, diff and documents](/reference/http-api/budget-and-events) |
| Editors | [`GET /api/editors`](/reference/http-api/repos-chains-harnesses-and-server#get-apieditors) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Editors | [`POST /api/documents/{id}/open`](/reference/http-api/repos-chains-harnesses-and-server#post-apidocumentsidopen) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Editors | [`POST /api/work-items/{id}/artifact/open`](/reference/http-api/repos-chains-harnesses-and-server#post-apiwork-itemsidartifactopen) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Editors | [`POST /api/work-items/{id}/artifacts/{kind}/open`](/reference/http-api/repos-chains-harnesses-and-server#post-apiwork-itemsidartifactskindopen) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Editors | [`POST /api/work-items/{id}/open-worktree`](/reference/http-api/repos-chains-harnesses-and-server#post-apiwork-itemsidopen-worktree) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Repos | [`GET /api/repos`](/reference/http-api/repos-chains-harnesses-and-server#get-apirepos) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Repos | [`POST /api/repos/probe`](/reference/http-api/repos-chains-harnesses-and-server#post-apireposprobe) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Repos | [`POST /api/repos`](/reference/http-api/repos-chains-harnesses-and-server#post-apirepos) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Repos | [`PATCH /api/repos?path=`](/reference/http-api/repos-chains-harnesses-and-server#patch-apirepospath) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Repos | [`DELETE /api/repos?path=`](/reference/http-api/repos-chains-harnesses-and-server#delete-apirepospath) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Chains and library | [`GET /api/templates/chains`](/reference/http-api/repos-chains-harnesses-and-server#get-apitemplateschains) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Chains and library | [`GET /api/templates/chains/{id}/resolved`](/reference/http-api/repos-chains-harnesses-and-server#get-apitemplateschainsidresolved) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Chains and library | [`GET /api/templates/library`](/reference/http-api/repos-chains-harnesses-and-server#get-apitemplateslibrary) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Chains and library | [`GET /api/templates/library/{ref}`](/reference/http-api/repos-chains-harnesses-and-server#get-apitemplateslibraryref) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Chains and library | [`GET /api/templates/steering/preview`](/reference/http-api/repos-chains-harnesses-and-server#get-apitemplatessteeringpreview) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Chains and library | [`POST /api/templates/reload`](/reference/http-api/repos-chains-harnesses-and-server#post-apitemplatesreload) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Harnesses | [`GET /api/harnesses/profiles`](/reference/http-api/repos-chains-harnesses-and-server#get-apiharnessesprofiles) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Harnesses | [`GET /api/harnesses`](/reference/http-api/repos-chains-harnesses-and-server#get-apiharnesses) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Harnesses | [`GET /api/harnesses/providers`](/reference/http-api/repos-chains-harnesses-and-server#get-apiharnessesproviders) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Settings | [`GET /api/policy`](/reference/http-api/drafts#get-apipolicy) | [Drafts](/reference/http-api/drafts) |
| Settings | [`PUT /api/policy`](/reference/http-api/drafts#put-apipolicy) | [Drafts](/reference/http-api/drafts) |
| Settings | [`GET /api/intake/checks?limit=`](/reference/http-api/drafts#get-apiintakecheckslimit) | [Drafts](/reference/http-api/drafts) |
| Server | [`GET /api/apply`](/reference/http-api/repos-chains-harnesses-and-server#get-apiapply) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Server | [`POST /api/apply/reload`](/reference/http-api/repos-chains-harnesses-and-server#post-apiapplyreload) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Server | [`POST /api/apply/restart`](/reference/http-api/repos-chains-harnesses-and-server#post-apiapplyrestart) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Server | [`GET /api/update`](/reference/http-api/repos-chains-harnesses-and-server#get-apiupdate) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Server | [`POST /api/update/check`](/reference/http-api/repos-chains-harnesses-and-server#post-apiupdatecheck) | [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server) |
| Drafts | [`GET /api/drafts`](/reference/http-api/drafts#get-apidrafts) | [Drafts](/reference/http-api/drafts) |
| Drafts | [`GET /api/drafts/{area}/{key}`](/reference/http-api/drafts#get-apidraftsareakey) | [Drafts](/reference/http-api/drafts) |
| Drafts | [`GET /api/drafts/{area}/{key}/fragment?path=`](/reference/http-api/drafts#get-apidraftsareakeyfragmentpath) | [Drafts](/reference/http-api/drafts) |
| Drafts | [`GET /api/drafts/policy/{key}/preview?chain=`](/reference/http-api/drafts#get-apidraftspolicykeypreviewchain) | [Drafts](/reference/http-api/drafts) |
| Drafts | [`PUT /api/drafts/{area}/{key}/files/{file}`](/reference/http-api/drafts#put-apidraftsareakeyfilesfile) | [Drafts](/reference/http-api/drafts) |
| Drafts | [`POST /api/drafts/{area}/{key}/ops`](/reference/http-api/drafts#post-apidraftsareakeyops) | [Drafts](/reference/http-api/drafts) |
| Drafts | [`POST /api/drafts/{area}/{key}/ops?preview=1`](/reference/http-api/drafts#post-apidraftsareakeyopspreview1) | [Drafts](/reference/http-api/drafts) |
| Drafts | [`POST /api/drafts/{area}/{key}/undo`](/reference/http-api/drafts#post-apidraftsareakeyundo) | [Drafts](/reference/http-api/drafts) |
| Drafts | [`POST /api/drafts/{area}/{key}/publish`](/reference/http-api/drafts#post-apidraftsareakeypublish) | [Drafts](/reference/http-api/drafts) |
| Drafts | [`POST /api/drafts/{area}/{key}/rebase`](/reference/http-api/drafts#post-apidraftsareakeyrebase) | [Drafts](/reference/http-api/drafts) |
| Drafts | [`DELETE /api/drafts/{area}/{key}`](/reference/http-api/drafts#delete-apidraftsareakey) | [Drafts](/reference/http-api/drafts) |
| Item drafts | [`GET /api/work-items/{id}/draft`](/reference/http-api/draft-ops#get-apiwork-itemsiddraft) | [Ops for each draft area](/reference/http-api/draft-ops) |
| Item drafts | [`PUT /api/work-items/{id}/draft`](/reference/http-api/draft-ops#put-apiwork-itemsiddraft) | [Ops for each draft area](/reference/http-api/draft-ops) |
| Item drafts | [`DELETE /api/work-items/{id}/draft`](/reference/http-api/draft-ops#delete-apiwork-itemsiddraft) | [Ops for each draft area](/reference/http-api/draft-ops) |
| Item drafts | [`POST /api/work-items/{id}/draft/apply`](/reference/http-api/draft-ops#post-apiwork-itemsiddraftapply) | [Ops for each draft area](/reference/http-api/draft-ops) |

## In this section

- [Work items](/reference/http-api/work-items): filing, reading, changing, deciding, pausing, ending and retrying a work item, and the routes a worker calls.
- [The work item object](/reference/http-api/work-item-object): the fields a work item answers carry, from its status and stop to its budget cap and plan progress.
- [Budget, events, diff and documents](/reference/http-api/budget-and-events): the daily spend total and raising a budget, the dry run of a create, events paging, the diff and review routes, and a work item's documents.
- [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server): connecting, changing and disconnecting a repo, chains and library components, harnesses, changes saved but not applied, the update check, and opening an editor.
- [Drafts](/reference/http-api/drafts): the routes the Templates and Settings editors use to keep, change, publish and discard a draft and the `result` each answers, and the policy and intake-check routes.
- [Ops for each draft area](/reference/http-api/draft-ops): every op a draft takes, by area, and a work item's own draft.
