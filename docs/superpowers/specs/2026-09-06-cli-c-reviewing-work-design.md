# CLI Sub-project C: Reviewing Work

> **Status:** design proposed 2026-09-06, not yet built. Depends on A.

## 1. Problem

A ships `kraft approve`. Approving without reading is worse than not having the
verb: the whole point of a gate is that a human looked. Reading today means the
browser — the diff panel and the linked-documents panel on the detail screen.

The server already has both: `GET /work-items/{wid}/diff` returns a unified diff
plus numstat plus untracked paths (truncated at a file boundary past
`DIFF_MAX_BYTES`), and `GET /work-items/{wid}/documents` +
`GET /documents/{doc_id}` return the specs, plans and session summaries the
indexer attached to the item.

Terminals are good at this. `git diff` has been read in one for thirty years,
and `delta`/`less -R` are already installed on the machine of anyone who would
use this.

## 2. Scope

Verbs: `kraft diff`, `kraft docs`, `kraft doc`. Three new `client.py` functions
returning plain values (no streaming — C is much simpler than B).

Out of scope: editing a document from the CLI, inline comments on a diff, and
per-file diff selection. `kraft diff | grep -A` covers the last one.

## 3. Verbs

**`kraft diff [ID] [--stat] [--name-only] [--no-pager]`** — the work item's
changes against `base_ref`. Default output is the unified diff, coloured, piped
to `$PAGER` when stdout is a tty. `--stat` prints just the numstat block, which
is the "how big is this" question you ask before deciding to read it.

Two things the API returns that a plain `git diff` would not, and which the CLI
must not swallow:

- **`truncated: true`** prints a final line naming the byte limit and the
  worktree path, so a reviewer knows the diff they just read was partial. A
  silent truncation at an approval gate is the worst failure this CLI could
  have.
- **`untracked`** paths are listed separately after the diff. An agent that
  wrote a new file without `git add` is normal mid-chain, and those files are
  invisible in a unified diff.

`base_ref: null` (pre-migration items, templates with no env_setup node) prints
"no baseline recorded for this work item" rather than an empty diff, because
empty and unknown are different answers.

**`kraft docs [ID]`** — the documents attached to the item: id, kind, path,
updated time. One line each, ids full so they can be pasted into the next
command.

**`kraft doc DOC_ID [--open [EDITOR]]`** — print the document to stdout (paged),
or with `--open` hand it to an editor. `--open` goes through
`POST /documents/{doc_id}/open`, so the CLI reuses the server's editor table and
its 501-when-headless behaviour rather than growing a second launcher.

## 4. Paging

One helper in `render.py`: `page(text)`. Uses `$PAGER`, falling back to
`less -R` when it exists, otherwise writes straight to stdout. Never pages when
stdout is not a tty or `--no-pager` is given. Colour goes through the same tty
check A defined, so a piped diff is plain text a patch tool can eat.

## 5. Testing

- `diff --stat` against a fixture work item with a worktree: numstat rendered,
  totals correct.
- `truncated: true` from the API produces the warning line. Asserted on the
  rendered output, not the payload — this is the check that the warning cannot
  be lost in a refactor.
- `untracked` files listed; `base_ref: null` prints the "no baseline" line.
- `docs`/`doc` round-trip: an indexed document is listed, then printed.
- `doc --open` against a headless server surfaces the 501 message as
  `kraft: ...`, exit 1, rather than a traceback.
- Paging is disabled under pytest (not a tty), so no test needs a pty.

## 6. Decisions

1. **`kraft diff` goes through the API**, not `git -C <worktree>`. Shelling out
   would dodge the byte limit, but it is a second data path that drifts from the
   API's shaping and is dead against a Kraft running anywhere but this machine.
   The truncation warning (§3) is the cheaper answer to the same problem. If
   large diffs become a real friction, raise `DIFF_MAX_BYTES` — one constant,
   both doors.
2. **No `kraft approve --review`.** `kraft diff <id>` then `kraft approve <id>`
   is two commands and needs no prompt. Keeping the CLI free of interactive
   prompts keeps every verb scriptable, which matters more than saving one line
   of typing.
