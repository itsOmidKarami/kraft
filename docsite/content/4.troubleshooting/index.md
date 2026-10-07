---
title: Troubleshooting
navigation:
  title: Overview
description: Where to look when a work item stopped, the server will not start, or a doctor row fails.
---

Find the message you are looking at in the first column, then follow its link.

## Find your problem

| What you see | Go to |
|---|---|
| A [work item](/concepts/vocabulary#work-item) under **Needs you**, with a reason such as `task failed in node ...`, `could not start ...`, `budget cap reached` or `stuck: ...` | [Why did my item stop?](/troubleshooting/why-did-my-item-stop#find-your-reason) |
| A sandbox, Kit, workspace-member or nested-repository reason, such as `a process in the sandbox was killed by its memory limit` or `the Kit ... cannot be used` | [Sandbox and Kit stops](/troubleshooting/sandbox-and-kit-stops#find-your-reason) |
| `no setup_command declared for ...` on a first work item | [`no setup_command declared`](/troubleshooting/why-did-my-item-stop#no-setup_command-declared) |
| `no user.name configured in ...` | [`no user.name configured`](/troubleshooting/why-did-my-item-stop#no-username-configured) |
| `... nothing registers it for Claude Code: ...` | [`nothing registers it for Claude Code`](/troubleshooting/why-did-my-item-stop#nothing-registers-it-for-claude-code) |
| `could not start ...` with `declares neither test_scopes nor test_command in repos.yaml` | [`declares neither test_scopes nor test_command`](/troubleshooting/why-did-my-item-stop#declares-neither-test_scopes-nor-test_command) |
| `task failed in node ...`, and the log shows an authentication or login error | [Questions, failed tasks and logins](/troubleshooting/why-did-my-item-stop#questions-failed-tasks-and-logins) |
| A work item that waits on CI or a review and nothing moves | [Waiting on CI or a review](/troubleshooting/why-did-my-item-stop#waiting-on-ci-or-a-review) |
| `kraft: command not found` | [`kraft`: command not found](/troubleshooting/starting-and-reaching-kraft#kraft-command-not-found) |
| `kraft: refusing to start`, or a port that is already in use | [Kraft won't start: port already in use](/troubleshooting/starting-and-reaching-kraft#kraft-wont-start-port-already-in-use) |
| A webhook or proxy gets 403 `unexpected Host` | [A webhook or proxy gets 403 "unexpected Host"](/troubleshooting/starting-and-reaching-kraft#a-webhook-or-proxy-gets-403-unexpected-host) |
| A `kraft admin doctor` row that prints `FAIL` or `warn` | [Doctor failures](/troubleshooting/doctor-failures#doctor-rows) |
| A page that is not where you left it, or a theme that looks different, after an upgrade from 1.4 | [Where the pages moved](/guides/run/upgrade-from-1-4#where-the-pages-moved) |
| A question about [forges](/concepts/vocabulary#forge), [harnesses](/concepts/vocabulary#harness), cost, platforms, specs, Kraft Lite or the permission gate | [FAQ](/get-started/faq) |
| None of these matches | [Getting help](/project/status-and-support#getting-help) |

## In this section

- [Why did my item stop?](/troubleshooting/why-did-my-item-stop): every other stop reason Kraft shows, with its fix, and the two waits that need no stop.
- [Starting and reaching Kraft](/troubleshooting/starting-and-reaching-kraft): a missing `kraft` command, a taken port, and a rejected `Host`.
- [Doctor failures](/troubleshooting/doctor-failures): every row `kraft admin doctor` can print, and how to clear it.
- [Sandbox and Kit stops](/troubleshooting/sandbox-and-kit-stops): the memory-limit, Kit, workspace-member and nested-repository stops of a sandboxed item.
