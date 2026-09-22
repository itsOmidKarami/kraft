# Inbound triggers

Start a chain from an event instead of typing into `kraft item create` every
time. Two doors, same effect — both always file the item `paused`, exactly
like manual intake: an agent cannot start work from a trigger any more than
from a person's own `kraft item create`.

## A cron schedule

Via a `triggers:` entry in `policy.yaml`:

```yaml
triggers:
  - cron: "0 9 * * 1,2,3,4,5"  # 5-field cron, minute resolution; 9am weekdays
    repo: /path/to/repo          # connect it first (`kraft repo connect`); an unconnected repo here is skipped with a logged warning, not filed
    chain: default              # a chain_template id from templates/
    title: "Nightly dependency check"
    description: "Filed by the 9am weekday trigger"  # optional, defaults to ""
```

Checked once a minute against the current time; a missed minute (server down,
clock skew) is not backfilled — that is treated as acceptable rather than an
incident. The poller itself runs for the life of the server, whether or not
any trigger existed at boot, so a trigger added afterward — through Settings,
or by hand followed by `kraft admin reload` — starts firing on its own
schedule without a restart.

## An HTTP call

Via `POST /api/triggers` — the HTTP twin of the same cron entry, for anything that
can fire a webhook (CI, an external scheduler, a script watching a queue) but
can't wait for the next minute-tick:

```
POST /api/triggers
{"title": "...", "repo": "/path/to/a/connected/repo", "chain_template": "default", "description": "..."}
```

`repo` must already be connected (`kraft repo connect`); a path Kraft has not
seen is refused with a 422 naming that command. A cron entry in
`policy.yaml` goes through the same connected-repo check, but there is no
request to answer: that trigger is skipped and a warning is logged naming
`kraft repo connect`, and the other triggers due in the same tick still fire.

It needs the same auth as every other mutating route — the session cookie a
browser holds after logging in, or an MCP bearer token — nothing
trigger-specific. See [Remote access](remote-access.md) for reaching this
endpoint from off-machine.
