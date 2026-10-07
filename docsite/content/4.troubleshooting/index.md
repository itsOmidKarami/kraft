---
title: Troubleshooting and FAQ
navigation:
  title: Overview
description: Where to look when a work item stopped, the server will not start, or a doctor row fails, and short answers to common questions.
---

Find the message you are looking at in the first column, then follow its link.

## Find your problem

| What you see | Go to |
|---|---|
| A [work item](/concepts/vocabulary#work-item) under **Needs you**, with a reason such as `task failed in node ...`, `could not start ...`, `budget cap reached`, `stuck: ...`, or a sandbox or Kit reason | [Why did my item stop?](/troubleshooting/why-did-my-item-stop#find-your-reason) |
| A work item that waits on CI or a review and nothing moves | [Waiting on CI or a review](/troubleshooting/why-did-my-item-stop#waiting-on-ci-or-a-review) |
| `kraft: command not found` | [`kraft`: command not found](/troubleshooting/starting-and-reaching-kraft#kraft-command-not-found) |
| `kraft: refusing to start`, or a port that is already in use | [Kraft won't start: port already in use](/troubleshooting/starting-and-reaching-kraft#kraft-wont-start-port-already-in-use) |
| A webhook or proxy gets 403 `unexpected Host` | [A webhook or proxy gets 403 "unexpected Host"](/troubleshooting/starting-and-reaching-kraft#a-webhook-or-proxy-gets-403-unexpected-host) |
| A `kraft admin doctor` row that prints `FAIL` or `warn` | [Doctor failures](/troubleshooting/doctor-failures#common-kraft-admin-doctor-failures) |
| A page that is not where you left it, or a theme that looks different, after an upgrade from 1.4 | [Where did a page go, and why does my theme look different?](/guides/run/upgrade-from-1-4#where-the-pages-moved) |
| A question about [forges](/concepts/vocabulary#forge), [harnesses](/concepts/vocabulary#harness), cost, platforms, specs, Kraft Lite or the permission gate | [FAQ](/troubleshooting/faq) |
| None of these matches | [Getting help](/project/status-and-support#getting-help) |

## In this section

- [Why did my item stop?](/troubleshooting/why-did-my-item-stop): every other stop reason Kraft shows, with its fix, and the two waits that need no stop.
- [Starting and reaching Kraft](/troubleshooting/starting-and-reaching-kraft): a missing `kraft` command, a taken port, and a rejected `Host`.
- [Doctor failures](/troubleshooting/doctor-failures): every row `kraft admin doctor` can print, and how to clear it.
- [FAQ](/troubleshooting/faq): short answers on what Kraft works with, what it costs, and how to write a spec.
- [Sandbox and Kit stops](/troubleshooting/sandbox-and-kit-stops): the memory-limit, Kit and workspace-member stops of a sandboxed item.
