# kraft

Eleven skills for driving [Kraft](https://github.com/itsOmidKarami/kraft) from a
Claude Code session, as `/kraft:<name>` slash commands. They call Kraft's MCP
server, so the `kraft` program must be installed and on your `PATH`. See the
[install guide](https://itsomidkarami.github.io/kraft/get-started/install).

## Install

```bash
claude plugin marketplace add itsOmidKarami/kraft
claude plugin install kraft@kraft
```

Then start Kraft (`kraft`), open a session in your repo, run this, and follow
what it asks:

```text
/kraft:onboard
```

To check it worked, run `/kraft:board`.

Update with `claude plugin update kraft`. Remove with
`claude plugin uninstall kraft`.

## The skills

| Skill | Use it to |
|---|---|
| `/kraft:onboard` | Connect a repo and verify the setup matches what the repo contains. |
| `/kraft:board` | See what is running, blocked, or waiting on a person, or search specs and plans. |
| `/kraft:prepare` | Write a spec, then decide whether the work runs inline or in Kraft. |
| `/kraft:handoff` | File work with Kraft, with any agreed spec and plan attached. It lands paused. |
| `/kraft:status` | Report where a work item has got to, or watch it until it ends. |
| `/kraft:gates` | Approve or reject the gate a work item is waiting on, or pause and resume it. |
| `/kraft:review` | Read what a gate is about and recommend approve or reject, with reasons. |
| `/kraft:steer` | Redirect a work item that is heading the wrong way. |
| `/kraft:triage` | Find out why a work item stopped and route it. |
| `/kraft:check` | Report where a repo's Kraft config has drifted from this version. |
| `/kraft:doctor` | Diagnose and fix an unhealthy Kraft server. |

An agent files work paused: a person starts it from the board. Details, the MCP
tools behind each skill, and the by-hand install are in the
[agent integration guide](https://itsomidkarami.github.io/kraft/guides/agent-integration).

Published from `plugins/kraft/` in the Kraft repository, alongside
[kraft-lite](../kraft-lite). The version is Kraft's own release tag, so plugin
`1.1.0` is the surface `kraft 1.1.0` serves.
