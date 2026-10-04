# kraft

Eleven skills for driving [Kraft](https://github.com/itsOmidKarami/kraft) from a
coding-agent session. Claude Code, Codex, Cursor and Antigravity install this
plugin. It also registers Kraft's MCP server, which the skills call, so the
`kraft` program must be installed and on your `PATH`. See the
[install guide](https://itsomidkarami.github.io/kraft/get-started/install).

## Install

### Claude Code

```bash
claude plugin marketplace add itsOmidKarami/kraft
claude plugin install kraft@kraft
```

Update with `claude plugin update kraft`. Remove with
`claude plugin uninstall kraft`.

### Codex

```bash
codex plugin marketplace add itsOmidKarami/kraft
codex plugin add kraft@kraft
```

Start a new Codex session to load the skills. Remove with
`codex plugin remove kraft@kraft`.

### Cursor

```bash
agent plugin marketplace add https://github.com/itsOmidKarami/kraft
```

Then start `agent`, run `/plugins`, open the Marketplace tab, pick `kraft`, and
install it.

### Antigravity

Antigravity imports the plugin from a local copy of the repo. The import copies
it, so the clone can go afterwards:

```bash
clone=$(mktemp -d)
git clone --depth 1 https://github.com/itsOmidKarami/kraft.git "$clone"
agy plugin import "$clone/plugins/kraft"
rm -rf "$clone"
```

To update, clone again and run the import with `--force`. Remove with
`agy plugin uninstall kraft`.

### Other agents

OpenCode, Amp and Gemini CLI have no plugin. They register the MCP server by
hand, and Amp adds these skills with `amp skill add`. The commands are on the
install guide's [agent tabs](https://itsomidkarami.github.io/kraft/get-started/install#connect-your-agent).

## Get started

Start Kraft (`kraft`), open a session in your repo, run the `onboard` skill,
and follow what it asks. In Claude Code that is:

```text
/kraft:onboard
```

In Codex, type `$` and pick `kraft:onboard`. To check it worked, run the
`board` skill (`/kraft:board`).

## The skills

The names are Claude Code's slash commands. Codex lists the same skills
without the slash (`kraft:board`).

| Skill | Use it to |
|---|---|
| `/kraft:onboard` | Connect a repo and verify the setup matches what the repo contains. |
| `/kraft:board` | See what is running, blocked, or waiting on a person, or search specs and plans. |
| `/kraft:prepare` | Write a spec, then decide whether the work runs inline or in Kraft. |
| `/kraft:handoff` | File work with Kraft, with any agreed spec and plan attached. It lands paused. |
| `/kraft:status` | Report where a work item has got to, or watch it until it ends. |
| `/kraft:gates` | Approve or reject the gate a work item is waiting on, leave review threads and submit a review, or pause and resume it. |
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
