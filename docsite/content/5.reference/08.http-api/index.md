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
| `storage` | `null` without `storage.worktrees.limit`, else `{state, used_bytes, quota_bytes, limit_bytes, measured_at}`. `state` is `ok`, `over_quota` or `held`, and `held` makes `status` `degraded`. |
| `intake_off` | Whether [auto-intake](/concepts/vocabulary#auto-intake) and `intake.yaml`'s schedules are off for it. |
| `plugin_updates` | Each auto-updating plugin's last update, by plugin id: `{at, outcome, kind, message}`. Empty when none has run. See [Auto-update](/reference/configuration/plugins#auto-update). |
| `run_dir`, `pid`, `uptime_s`, `bind`, `port` | Which instance this is. |
| `version` | The version it runs. |
| `installed` | The version installed on disk, which differs until a restart finishes an update. |
| `reattach_summary` | What startup found of agent sessions that were running: `scanned`, `adopted`, `resolved_from_file`, `unknown` and `resumed_work_items`. |
| `index` | The search index: `last_scan_at`, `repos_scanned`, `documents`, and the state of `embeddings`. |
| `session_expiry_days` | How long a login lasts, which the login screen shows. |

A plugin that is installed and did not load is under `invalid_templates`, as `plugin <id>` with the reason, and `status` is `degraded`. A held or failed entry of `plugin_updates` leaves `status` as it is.

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
- reading and saving the theme, access, notify and intake settings, sending a test notification, saving the library, reading and saving a chain's file, and the checks that run on them;
- reading one harness provider's Settings view, and reading and saving one harness profile's.

A route listed here is written in full: its method and its path with the `/api` prefix, as in `POST /api/work-items`. That is how the route appears in `/openapi.json` and in a request. A route that is in none of the tables below, nor in the [stable routes](#stable-routes), has no entry.

The pages can change in any release. A script that must keep working calls the [stable routes](#stable-routes), or the `kraft` command with `--json`. Each table below links a route to its entry.

### Work items

| Route | What it does |
|---|---|
| [`POST /api/work-items`](/reference/http-api/work-items#post-apiwork-items) | Files a work item, paused unless the body starts it. |
| [`POST /api/work-items/{id}/duplicate`](/reference/http-api/work-items#post-apiwork-itemsidduplicate) | Files a fresh paused item from an existing one. |
| [`GET /api/work-items`](/reference/http-api/work-items#get-apiwork-items) | Lists the items on the board. |
| [`GET /api/work-items/{id}`](/reference/http-api/work-items#get-apiwork-itemsid) | Answers one item in full. |
| [`PATCH /api/work-items/{id}`](/reference/http-api/work-items#patch-apiwork-itemsid) | Changes an item's title, description, chain, overrides, budget, policy or attachments. |
| [`POST /api/work-items/bulk`](/reference/http-api/work-items#post-apiwork-itemsbulk) | Pauses, cancels, archives or restores several items. |
| [`POST /api/work-items?dry_run=1`](/reference/http-api/budget-and-events#post-apiwork-itemsdry_run1) | Runs every check of a create and files nothing. |

### Gates

| Route | What it does |
|---|---|
| [`POST /api/work-items/{id}/gates/{gate}/approve`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidgatesgateapprove) | Approves the pending gate and starts the chain again. |
| [`POST /api/work-items/{id}/gates/{gate}/reject`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidgatesgatereject) | Rejects the pending gate and sends the chain back to work. |

### Run control

| Route | What it does |
|---|---|
| [`POST /api/work-items/{id}/pause`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidpause) | Stops a running item and ends its sessions. |
| [`POST /api/work-items/{id}/steer`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidsteer) | Stores a note for the next agent launch of a paused item. |
| [`POST /api/work-items/{id}/resume`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidresume) | Starts a paused item, with a note if you give one. |
| [`POST /api/work-items/{id}/skip`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidskip) | Advances past the current node or gate without running it. |
| [`POST /api/work-items/{id}/escalate`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidescalate) | Sends a message to an agent that helps with a stopped item. |
| [`POST /api/work-items/{id}/escalate/stop`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidescalatestop) | Ends the running escalation turn. |
| [`POST /api/work-items/{id}/retry`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidretry) | Reruns the node an item stopped on. |
| [`POST /api/work-items/{id}/reopen-mr`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidreopen-mr) | Reopens a merge request closed on the forge, then retries. |
| [`POST /api/work-items/{id}/progress`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidprogress) | Records that a worker started a plan task. |
| [`POST /api/work-items/{id}/mr-labels`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidmr-labels) | Labels the item's merge request. |

### Ending

| Route | What it does |
|---|---|
| [`GET /api/work-items/{id}/cancel-preview`](/reference/http-api/gates-run-control-and-ending#get-apiwork-itemsidcancel-preview) | Shows what a cancel would do, without doing it. |
| [`POST /api/work-items/{id}/cancel`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidcancel) | Ends an item and keeps its worktree and branch. |
| [`POST /api/work-items/{id}/complete`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidcomplete) | Ends an item as completed, by hand. |
| [`POST /api/work-items/{id}/archive`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidarchive) | Archives a completed or cancelled item and reclaims its worktree. |
| [`POST /api/work-items/{id}/restore`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidrestore) | Puts an archived item back under Done. |
| [`POST /api/work-items/{id}/abandon`](/reference/http-api/gates-run-control-and-ending#post-apiwork-itemsidabandon) | Ends an item and deletes its worktree and branch. |

### Budget

| Route | What it does |
|---|---|
| [`GET /api/budget/today`](/reference/http-api/budget-and-events#get-apibudgettoday) | Answers the instance's spend since local midnight against the daily cap. |
| [`POST /api/work-items/{id}/budget/raise`](/reference/http-api/budget-and-events#post-apiwork-itemsidbudgetraise) | Raises the dollar cap that stopped an item and retries it. |

### Events

| Route | What it does |
|---|---|
| [`GET /api/work-items/{id}/events`](/reference/http-api/budget-and-events#get-apiwork-itemsidevents) | Reads an item's events, forward or backward. |
| [`WS /api/ws/events`](/reference/events#ws-apiwsevents) | Streams every item's events as they are stored, replaying from `after_seq` first. |

### Diff and review

| Route | What it does |
|---|---|
| [`GET /api/work-items/{id}/diff`](/reference/http-api/budget-and-events#get-apiwork-itemsiddiff) | Answers the changes the agents made. |
| [`GET /api/work-items/{id}/compare`](/reference/http-api/budget-and-events#get-apiwork-itemsidcompare) | Diffs any two review targets. |
| [`PUT /api/work-items/{id}/viewed`](/reference/http-api/budget-and-events#put-apiwork-itemsidviewed) | Marks a file viewed. |
| [`DELETE /api/work-items/{id}/viewed`](/reference/http-api/budget-and-events#delete-apiwork-itemsidviewed) | Clears the mark. |
| [`GET /api/work-items/{id}/fix-target`](/reference/http-api/budget-and-events#get-apiwork-itemsidfix-target) | Answers where a request-changes review would restart. |
| [`POST /api/work-items/{id}/review`](/reference/http-api/budget-and-events#post-apiwork-itemsidreview) | Submits a review. Its entry documents only `digest`. |
| [`POST /api/work-items/{id}/gates/{gate}/review`](/reference/http-api/budget-and-events#post-apiwork-itemsidreview) | Submits a review for a named gate, as `/review` does for the pending one. |

### Documents

| Route | What it does |
|---|---|
| [`GET /api/work-items/{id}/attachments/{kind}`](/reference/http-api/budget-and-events#get-apiwork-itemsidattachmentskind) | Reads the spec or plan attached at intake. |
| [`GET /api/work-items/{id}/documents`](/reference/http-api/budget-and-events#get-apiwork-itemsiddocuments) | Lists the documents linked to an item. |
| [`GET /api/work-items/{id}/artifact`](/reference/http-api/budget-and-events#get-apiwork-itemsidartifact) | Reads the pending gate's document. |
| [`GET /api/work-items/{id}/artifacts/{kind}`](/reference/http-api/budget-and-events#get-apiwork-itemsidartifactskind) | Reads the document a task wrote. |

### Opening in an editor

| Route | What it does |
|---|---|
| [`GET /api/editors`](/reference/http-api/repos-chains-harnesses-and-server#get-apieditors) | Lists the editors this machine can open. |
| [`POST /api/documents/{id}/open`](/reference/http-api/repos-chains-harnesses-and-server#post-apidocumentsidopen) | Opens a document in an editor. |
| [`POST /api/work-items/{id}/artifact/open`](/reference/http-api/repos-chains-harnesses-and-server#post-apiwork-itemsidartifactopen) | Opens the pending gate's document. |
| [`POST /api/work-items/{id}/artifacts/{kind}/open`](/reference/http-api/repos-chains-harnesses-and-server#post-apiwork-itemsidartifactskindopen) | Opens a task's document. |
| [`POST /api/work-items/{id}/open-worktree`](/reference/http-api/repos-chains-harnesses-and-server#post-apiwork-itemsidopen-worktree) | Opens the item's worktree. |

### Storage

| Route | What it does |
|---|---|
| [`GET /api/storage`](/reference/http-api/repos-chains-harnesses-and-server#get-apistorage) | Answers the disk the worktrees use, item by item. |
| [`POST /api/storage/preview`](/reference/http-api/repos-chains-harnesses-and-server#post-apistoragepreview) | Shows what archiving some items would free and lose. |

### Repos

| Route | What it does |
|---|---|
| [`GET /api/repos`](/reference/http-api/repos-chains-harnesses-and-server#get-apirepos) | Lists the connected repos and workspaces. |
| [`POST /api/repos/probe`](/reference/http-api/repos-chains-harnesses-and-server#post-apireposprobe) | Reads a repository and proposes how to connect it. |
| [`POST /api/repos`](/reference/http-api/repos-chains-harnesses-and-server#post-apirepos) | Connects a repository. |
| [`PATCH /api/repos?path=`](/reference/http-api/repos-chains-harnesses-and-server#patch-apirepospath) | Changes a connected repo's entry in `repos.yaml`. |
| [`DELETE /api/repos?path=`](/reference/http-api/repos-chains-harnesses-and-server#delete-apirepospath) | Disconnects a repo. |

### Chains and library

| Route | What it does |
|---|---|
| [`GET /api/templates/chains`](/reference/http-api/repos-chains-harnesses-and-server#get-apitemplateschains) | Lists every chain. |
| [`GET /api/templates/chains/{id}/resolved`](/reference/http-api/repos-chains-harnesses-and-server#get-apitemplateschainsidresolved) | Answers one chain with its `extends` expanded. |
| [`GET /api/templates/library`](/reference/http-api/repos-chains-harnesses-and-server#get-apitemplateslibrary) | Answers `library.yaml` and every component in it. |
| [`GET /api/templates/library/{ref}`](/reference/http-api/repos-chains-harnesses-and-server#get-apitemplateslibraryref) | Answers one library component. |
| [`GET /api/templates/steering/preview`](/reference/http-api/repos-chains-harnesses-and-server#get-apitemplatessteeringpreview) | Shows what an agent task reads at launch. |
| [`POST /api/templates/reload`](/reference/http-api/repos-chains-harnesses-and-server#post-apitemplatesreload) | Rereads the library, `policy.yaml` and `intake.yaml`, and answers what it refused. |

### Harnesses and providers

| Route | What it does |
|---|---|
| [`GET /api/harnesses/profiles`](/reference/http-api/repos-chains-harnesses-and-server#get-apiharnessesprofiles) | Answers the harness profiles and the agent profiles. |
| [`GET /api/harnesses`](/reference/http-api/repos-chains-harnesses-and-server#get-apiharnesses) | Lists each provider and whether its CLI is installed. |
| [`GET /api/harnesses/providers`](/reference/http-api/repos-chains-harnesses-and-server#get-apiharnessesproviders) | Answers each provider's capabilities. |

### Settings

| Route | What it does |
|---|---|
| [`GET /api/policy`](/reference/http-api/drafts#get-apipolicy) | Reads `policy.yaml` from disk. |
| [`PUT /api/policy`](/reference/http-api/drafts#put-apipolicy) | Saves `policy.yaml` without a draft, and applies it. |
| [`GET /api/intake/checks?limit=`](/reference/http-api/drafts#get-apiintakecheckslimit) | Lists what recent intake polls found. |

### Server

| Route | What it does |
|---|---|
| [`GET /api/apply`](/reference/http-api/repos-chains-harnesses-and-server#get-apiapply) | Lists what is saved and not running yet. |
| [`POST /api/apply/reload`](/reference/http-api/repos-chains-harnesses-and-server#post-apiapplyreload) | Rereads the library, `policy.yaml` and `intake.yaml`. |
| [`POST /api/apply/restart`](/reference/http-api/repos-chains-harnesses-and-server#post-apiapplyrestart) | Restarts a server that a service manager started. |
| [`GET /api/update`](/reference/http-api/repos-chains-harnesses-and-server#get-apiupdate) | Answers the cached update check. |
| [`POST /api/update/check`](/reference/http-api/repos-chains-harnesses-and-server#post-apiupdatecheck) | Asks the release feed now. |

### Config drafts

| Route | What it does |
|---|---|
| [`GET /api/drafts`](/reference/http-api/drafts#get-apidrafts) | Lists every open draft. |
| [`GET /api/drafts/{area}/{key}`](/reference/http-api/drafts#get-apidraftsareakey) | Reads a draft, or the published files when there is none. |
| [`GET /api/drafts/{area}/{key}/fragment?path=`](/reference/http-api/drafts#get-apidraftsareakeyfragmentpath) | Reads one component of a draft as YAML. |
| [`GET /api/drafts/policy/{key}/preview?chain=`](/reference/http-api/drafts#get-apidraftspolicykeypreviewchain) | Shows a published chain's caps under the `policy` draft. |
| [`PUT /api/drafts/{area}/{key}/files/{file}`](/reference/http-api/drafts#put-apidraftsareakeyfilesfile) | Stores one file's text as typed. |
| [`POST /api/drafts/{area}/{key}/ops`](/reference/http-api/drafts#post-apidraftsareakeyops) | Applies a list of ops, all or nothing. |
| [`POST /api/drafts/{area}/{key}/ops?preview=1`](/reference/http-api/drafts#post-apidraftsareakeyopspreview1) | Answers what a list of ops would do, and saves nothing. |
| [`POST /api/drafts/{area}/{key}/undo`](/reference/http-api/drafts#post-apidraftsareakeyundo) | Undoes the last request. |
| [`POST /api/drafts/{area}/{key}/publish`](/reference/http-api/drafts#post-apidraftsareakeypublish) | Writes the draft's files and applies them. |
| [`POST /api/drafts/{area}/{key}/rebase`](/reference/http-api/drafts#post-apidraftsareakeyrebase) | Keeps the draft's text over files that changed on disk. |
| [`DELETE /api/drafts/{area}/{key}`](/reference/http-api/drafts#delete-apidraftsareakey) | Discards a draft for good. |

### A work item's draft

| Route | What it does |
|---|---|
| [`GET /api/work-items/{id}/draft`](/reference/http-api/draft-ops#get-apiwork-itemsiddraft) | Reads an item's draft. |
| [`PUT /api/work-items/{id}/draft`](/reference/http-api/draft-ops#put-apiwork-itemsiddraft) | Replaces an item's op list. |
| [`DELETE /api/work-items/{id}/draft`](/reference/http-api/draft-ops#delete-apiwork-itemsiddraft) | Discards an item's draft. |
| [`POST /api/work-items/{id}/draft/apply`](/reference/http-api/draft-ops#post-apiwork-itemsiddraftapply) | Applies an item's draft. |

## In this section

- [Filing, reading and changing work items](/reference/http-api/work-items): filing and duplicating an item, reading the board and one item, changing an item, and acting on several at once.
- [Gates, run control, ending and retry](/reference/http-api/gates-run-control-and-ending): deciding a gate, pausing, resuming, skipping and escalating, ending and archiving an item, retrying it, and the routes a worker calls.
- [The work item object](/reference/http-api/work-item-object): the three shapes a work item answers in, its stored columns, and the fields that need an explanation.
- [Budget, events, diff and documents](/reference/http-api/budget-and-events): the daily spend total and raising a budget, the dry run of a create, events paging, the diff and review routes, and a work item's documents.
- [Repos, chains, harnesses and the server](/reference/http-api/repos-chains-harnesses-and-server): connecting, changing and disconnecting a repo, chains and library components, harnesses, changes saved but not applied, the update check, and opening an editor.
- [Drafts](/reference/http-api/drafts): the routes the Templates and Settings editors use to keep, change, publish and discard a draft and the `result` each answers, and the policy and intake-check routes.
- [Ops for each draft area](/reference/http-api/draft-ops): every op a draft takes, by area, and a work item's own draft.
