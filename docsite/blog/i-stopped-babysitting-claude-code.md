---
title: "I stopped babysitting Claude Code: spec in, merge request out"
description: Why I built Kraft, one real run from hand-off to merge request, and what keeps the agent honest when nobody is watching.
date: 2026-11-11
author: Omid Karami
image: /blog/i-stopped-babysitting-claude-code/cover.png
tags: [launch, claude-code]
---

I use Claude Code on real repos every day, often in two or three sessions at once. The code was rarely the problem; I was. I answered `(y/n)` prompts, re-ran tests it said had passed, and reminded it what we had agreed an hour earlier. I was the scheduler, the reviewer, the test runner and the memory. When I closed the terminal, the work stopped.

I built [Kraft](https://github.com/itsOmidKarami/kraft), an open-source software factory that runs on your machine. It isn't another coding agent. It runs the one you already use, Claude Code by default, from spec to merge request, and stops only when a decision is yours.

::site-video
---
id: PPr7e5-1yws
title: "Kraft: stop babysitting your coding agents!"
length: "1:53"
label: "Play the video: Kraft, stop babysitting your coding agents, 1 minute 53 seconds"
---
::

## One real run

This week Kraft built a new home page for its own docs site: 30 files, +3,044/−466.

1. **I settled the spec and plan in a session**, the way I always work with Claude Code.
2. **I filed them with the work item.** An attached spec or plan counts as decided, so Kraft dropped those two gates. Filed work waits paused and spends nothing until you start it.
3. **I walked away.** Claude Code implemented the plan in its own git worktree, on its own branch, and left my checkout untouched. Kraft then ran the tests in a step the agent doesn't control. When tests fail, a repair step fixes the code; from round two, a judge decides whether another round is worth it. This run needed two.
4. **It stopped for my review** of the diff, then opened the merge request and ran CI and automated review.

![The fix loop: tests, repair, judge](/assets/fixloop.png)

Hand-off to merge request took 69 minutes and $10.20. That's above the $10 default cap; I run mine higher. Kraft checks the cap before each agent step starts and won't stop one mid-run.

When I approve the final review, Kraft merges only the commit I approved. It carries that approval across its own clean rebases; any other change cancels the merge or stops the item.

## Kraft builds Kraft

Most of Kraft's changes now go through Kraft. While I wrote this, my board had seven items running on three chains, two waiting on other items and ten done.

![My board while I wrote this: seven work items running, two not started because they wait on another item, and ten done](/blog/i-stopped-babysitting-claude-code/board.png)

Three lessons:

- It doesn't make the work faster. It ends the babysitting, which is worth more.
- "Tests pass" from an agent meant nothing until a step it doesn't control ran them.
- Not every change needs it. Quick fixes still belong in a session.

## What keeps it honest

- **A chain, not improvisation.** The steps live in a YAML file you can read and diff, and Kraft runs them in that order. A separate reviewer or judge, not the agent that wrote the fix, decides when a fix is done.
- **You at the decisions.** Kraft stops for you at the spec, the plan and the final review. It refuses an agent's calls to decide its own gate, but an unsandboxed worker runs as you and could get around that. A sandbox with a `network:` policy makes the gate a wall; [the security page](/project/security-in-depth#agents-and-gates) draws the line.
- **Caps.** By default a work item can spend $10 and the whole instance $50 a day. A capped item stops and hands you the full trace.
- **Local.** Kraft runs on your machine, bound to localhost. A tunnel brings the board to your phone. Close the laptop and local work pauses.

## When to skip it

For a quick fix, an idea you haven't pinned down, or work you want to steer every few minutes, use a session. My pattern: think in a session, settle the spec and plan, hand them to Kraft, and start the next thing.

## Try it

- Demo, nothing to install: [kraft-sdlc.dev/demo](https://kraft-sdlc.dev/demo/)
- Install: `uv tool install kraft-sdlc && kraft` (macOS or Linux, GitHub or GitLab)
- Repo: [github.com/itsOmidKarami/kraft](https://github.com/itsOmidKarami/kraft)

Want to contribute? [Start here](https://github.com/itsOmidKarami/kraft/issues/1131) lists small, self-contained issues.

Tell me where it breaks for you, in [GitHub Discussions](https://github.com/itsOmidKarami/kraft/discussions).
