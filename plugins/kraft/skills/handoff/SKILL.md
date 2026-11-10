---
name: handoff
description: "Files work with Kraft as a paused work item, with any agreed spec and plan attached. Use when the person says work settled in this session should go to Kraft instead of being done here, or to file it, hand it off or put it on the board. It connects a repo Kraft has not seen first; verifying that repo's test and setup commands belongs to kraft:onboard, the inline-or-Kraft decision to kraft:prepare."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://kraft-sdlc.dev/get-started/install rather than reporting a
connection error.

# Handing work to Kraft

Kraft runs semi-autonomous work items as chains, with human gates. File work
once the person has said to hand it off, or `kraft:prepare` judged it a handoff
and they agreed. Neither: run `kraft:prepare` first.

1. `ensure_repo()` - connects the current repo if Kraft has not seen it.
   Idempotent, so call it every time rather than checking first. If the answer
   says `already_connected: false`, say so, file anyway, and offer
   `kraft:onboard` before the item is started: the test and setup commands it
   proposed are saved unconfirmed, and an item run against wrong ones stops.
2. `create_work_item(title, description=...)` - files the work, with any spec
   and plan attached (below). The title is a label; the description is the
   brief, and it is what the spec node writes its design from. The worker never
   sees this session, so the description carries the goal, what was agreed,
   what's out of scope and how to tell it's done. Work in parts that build on
   each other: file the earlier part first and pass its id as `depends_on` to
   the next, since it can't be added later.
3. Read the answer. On a disabled repo it is refused (`403`, "… is disabled"):
   tell the person what the answer says the repo needs, and don't retry until
   they have enabled it in Settings › Repos. A `422` filed nothing: fix the
   attachment it names and file again. A `duplicate_warning` comes back on a
   successful create, so the new item exists too: give the person both ids and
   let them pick; abandoning one is theirs (`kraft item abandon`). A
   `repo_warning` means the repo will stop the item once started: pass it on.

**`create_work_item` does not start anything.** The item lands paused and a
person starts it from the board, or asks you to (`resume_work_item()`, see
`kraft:gates`); `kraft:status` watches it. When you report back, say the work
is *filed*, not that it is underway - telling someone their work is running
when nothing is running is the one failure this whole surface is built to
avoid.

## When the spec and plan already exist

If documents were written in this session or already live in the repo, attach
them at intake instead of letting Kraft re-run those phases. A spec agreed only
in chat is written to a file first, then attached:

    create_work_item(title, description=..., attachments=[
        {"kind": "spec", "path": ".engineering/specs/x.md"},
        {"kind": "plan", "path": ".engineering/plans/x.md"},
    ])

Kind is `spec` or `plan`, at most one of each. A path is resolved against the
repo and against the working tree you are standing in, so a document written in
a worktree can be attached exactly as you wrote it - relative to that tree, or
absolute.

Attaching a spec or plan trims the node whose gate it satisfies, so the person
is not asked to re-approve what they just agreed with you, and the implementing
agent is told to follow the documents rather than guess.

Kraft copies an attached document at intake, so editing your file afterwards
changes nothing. To revise one before the item starts, `kraft:steer` re-attaches
it rather than filing again.

Before attaching a plan, skim it for a full-test-suite step (e.g. "run the full
test suite" / "run all tests" as a task, not a task's own targeted test). The
chain's verification node already runs the repo's tests after implementation,
with its own fix loop, so a plan step doing the same is redundant. If you see
one, mention to the user that it's not advised and offer to strip it before
attaching.

## Confirm it landed

Run `kraft view list --all --status=paused` (or `get_work_item(<the new id>)`) and
check the item is there with the title you gave and, if you attached documents,
that `kraft view docs ID` lists them.
