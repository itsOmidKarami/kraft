# Kraft

[![test](https://github.com/itsOmidKarami/kraft/actions/workflows/test.yml/badge.svg)](https://github.com/itsOmidKarami/kraft/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/kraft-sdlc)](https://pypi.org/project/kraft-sdlc/)
[![Latest release](https://img.shields.io/github/v/release/itsOmidKarami/kraft)](https://github.com/itsOmidKarami/kraft/releases)
[![License](https://img.shields.io/github/license/itsOmidKarami/kraft)](https://github.com/itsOmidKarami/kraft/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-kraft--sdlc.dev-blue)](https://kraft-sdlc.dev/)

**A local orchestrator that takes your coding agent from spec to pull request,
stopping only when a decision is yours.**

Kraft isn't another coding agent. It runs the one you already use, and adds
what a single session can't: a process the agent can't skip, checks it doesn't
grade itself on, and a person at the decisions that matter.

Hand Kraft a task, start it and walk away. A new work item is filed paused,
in the web interface, on a phone and from the CLI alike, so nothing runs until
you start it. Your agent drafts the spec and the plan and
stops for your approval of each, or starts from a spec you wrote. It's for
developers tired of babysitting a session to the end of a task.

Kraft works with Claude Code out of the box, and you can drive it from Codex,
Cursor, Antigravity, OpenCode, Amp or Gemini CLI too. The shipped chains run
their agent steps on Claude Code, and any step can
[switch to another agent](https://kraft-sdlc.dev/guides/switch-harness).

**New in 2.0:** a redesigned web interface built around what needs you, a
review page that works like a code host's, and a `kraft repo connect` that
reads a repo's task runner, toolchain and CI, explains each command it
proposes, and rehearses them with `--verify`.
[What's new in 2.0](https://kraft-sdlc.dev/next/get-started/whats-new)
has the rest, and what to check before you upgrade from 1.4.

![The Kraft board in the dark graphite look: filters for chain, gate and stop reason above work items grouped by Needs you (a paused item with Resume, a failed one with Retry, and two waiting at a gate, each with its one button), Running and Not started, the unstarted items marked created paused, with a Select all link on every group and a ? shortcuts button in the toolbar](https://raw.githubusercontent.com/itsOmidKarami/kraft/main/.github/assets/board.png)

- **Gates where a human decides.** Kraft pauses at a spec, a plan, or a merge
  request for your approval; request changes with a note and the node that wrote it
  tries again.
- **Capped retries, not runaway loops.** Attempts, wall-clock time, and spend
  are all bounded. Hit a cap and the item stops and hands you the full trace
  instead of quietly burning tokens.
- **Isolated git worktrees.** Each work item runs on its own branch in its own
  worktree, so your checkout stays as you left it and items run in parallel.
- **Local.** One process on your machine, bound to loopback by default — not a
  hosted service.
- **A board you can check from your phone.** Reach it from another device
  through a tunnel.

New here? [Why Kraft](https://kraft-sdlc.dev/get-started/why-kraft) covers what it does that a session, a loop or a skill does not, and when not to use it.

*Kraft* is German for force; in English, its cousin *craft* means skill. Your agent brings the craft; Kraft is the force that carries it from spec to pull request.

**A gate stops the chain where a human decides.** Read what the agent wrote,
then approve, or request changes with a note that re-runs the node that wrote it.

![The gate's review page headed Approve spec: Request changes and Approve buttons in the header, a Waiting for you mark, a line saying Approving starts plan with the gates left and the spend, the spec an agent wrote rendered below, and the files on the branch with one pending comment](https://raw.githubusercontent.com/itsOmidKarami/kraft/main/.github/assets/gate.png)

**Read by name, not by id.** Nodes show as "approve spec" and "chain revision",
and a chain too long to read whole gets an overview of all of it, one small
glyph per node with a frame on the part in view, above the full-size graph. The
board filters by chain, gate and stop reason, and retries or resumes a
selection in one go. Settings keeps code and board preferences on their own
Board & code page. Every page warns when Kraft is offline, and `?` lists the
keyboard shortcuts.

<table>
<tr>
<td width="60%">

**Same app, phone-sized.** Below 768 pixels it switches to a phone layout on its own. A gate's card opens its review, so nothing is approved from the list, and Chains edits drafts there too. Reach it from another device through a tunnel; see
[Remote access](https://kraft-sdlc.dev/guides/remote-access).

</td>
<td width="40%">

![The board at a 390px phone viewport, with bottom tab navigation, filter chips and Chain, Gate and Stop reason menus, and cards for a paused item with Resume, a failed one with Retry, and gate-waiting items whose Review to approve button opens the review](https://raw.githubusercontent.com/itsOmidKarami/kraft/main/.github/assets/mobile.png)

</td>
</tr>
</table>

Each work item follows a
[chain](https://kraft-sdlc.dev/concepts/vocabulary): a list of
nodes such as write the spec, write the plan, implement, verify and open the
pull request. Some nodes run your coding agent, some run a command, and some
stop and wait for you. Chains are YAML files you can edit. A team can share
chains as [Kraft add-ons](https://kraft-sdlc.dev/next/guides/customize/share-chains-with-addons),
which each instance installs from a git repository or a directory. How the
pieces fit together is in
[Architecture](https://kraft-sdlc.dev/project/architecture).

## Requirements

- **macOS or Linux.** CI runs on Linux, and `kraft admin install-service`
  supports launchd and systemd only. Windows is not supported, and WSL is
  untested.
- **`git`**, with `user.name` and `user.email` set (globally, or in each repo
  you connect). Kraft commits the agents' work under that identity.
- **[uv](https://docs.astral.sh/uv/)**, which uses a Python 3.12 or newer
  already on your machine, or fetches one. Or Homebrew on macOS.
- **[Claude Code](https://code.claude.com/docs)**, installed
  and logged in. Every agent task in the shipped chains runs on it.
- **`gh` or `glab`**, logged in, for the nodes that open and merge the pull
  request on GitHub or GitLab.

**Supported agents:** Claude Code, Codex, Cursor, Gemini, Antigravity, OpenCode
and Amp all run as [harnesses](https://kraft-sdlc.dev/reference/harnesses).
The shipped chains use Claude Code only; to use another agent, edit the chains.

## What it costs

Kraft is free and Apache-2.0 licensed. Your agent provider bills you for the
sessions Kraft runs. The shipped `policy.yaml` caps spend at $10 per work item
and $50 per day across all items, and `kraft item create --budget USD` sets one
item's cap. A cap stops the next agent task from starting, so spend can go over
it by the cost of the task already running. In testing, a small fix on the
`quick-task` chain cost a few cents. See
[Caps and budgets](https://kraft-sdlc.dev/concepts/caps-and-budgets).

## Install

Install Kraft with `uv`, or with Homebrew on macOS:

```bash
uv tool install kraft-sdlc
# or
brew tap itsOmidKarami/kraft && brew install kraft
```

To drive Kraft from Claude Code, install the plugin:

```bash
claude plugin marketplace add itsOmidKarami/kraft
claude plugin install kraft@kraft
```

Codex, Cursor and Antigravity install the same plugin; their commands are on
the [install guide's agent tabs](https://kraft-sdlc.dev/get-started/install#connect-your-agent).
If you drive Kraft from one of them, install the Claude Code plugin (or run
`kraft admin init`) as well: the shipped chains run their workers on Claude
Code.

Start the server and leave it running in its own terminal:

```bash
kraft              # http://127.0.0.1:8765
kraft --version    # in another terminal: confirms what you installed
```

Then open a Claude Code session in your repo, run this, and follow what it
asks. It needs the server running:

```text
/kraft:onboard
```

To clear gates and review diffs from VS Code, install the
[Kraft extension](https://kraft-sdlc.dev/guides/vscode) from the
[VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=kraft-sdlc.kraft),
or from [Open VSX](https://open-vsx.org/extension/kraft-sdlc/kraft) in VSCodium
and Cursor (publisher `kraft-sdlc` on both). You can also download
`kraft-<version>.vsix` from the latest
[GitHub release](https://github.com/itsOmidKarami/kraft/releases/latest) and run
`code --install-extension kraft-<version>.vsix`.

Every install path, connecting your agent, and updating are in the
[install guide](https://kraft-sdlc.dev/get-started/install). Coming
from 1.4? Update with `uv tool install`, not `kraft admin update`: see
[Upgrading from 1.4](https://kraft-sdlc.dev/next/get-started/install#upgrading-from-14).

## First run

Open http://127.0.0.1:8765. With no repo connected, a short setup walks you
through connecting one, the chain and policy, and a first work item, which is
created paused. Then follow the
[first-work-item tutorial](https://kraft-sdlc.dev/get-started/first-work-item)
to file your first work item.

## Analytics

Lead time, cost, and where both go — by node and by repo, over a Range you pick:
7 days, 30 days, 8 weeks (the default) or 90 days, the same menu on a desktop and a phone. Built from the same events the
board renders live, not a separate pipeline.

![The Analytics view over 8 weeks, with Range, Repo and Chain menus: completed count, median lead time, cost and repairs per item; merged items per week; a Waiting at each gate table with waits, median, total and rejected for each gate; the top of the By node cost table](https://raw.githubusercontent.com/itsOmidKarami/kraft/main/.github/assets/analytics.png)

## Where to go next

- [Concepts](https://kraft-sdlc.dev/concepts/vocabulary): work items, chains, nodes, gates, caps.
- [CLI reference](https://kraft-sdlc.dev/reference/cli): every `kraft` verb.
- [Configuration](https://kraft-sdlc.dev/reference/configuration): `repos.yaml`, `policy.yaml`, `access.yaml`, and the state directory (`$KRAFT_HOME`).
- [Agent integration](https://kraft-sdlc.dev/guides/agent-integration): MCP tools and skills.
- [Share chains with add-ons](https://kraft-sdlc.dev/next/guides/customize/share-chains-with-addons): publish chains, skills and agent profiles from a git repository, and install a team's.
- [Remote access](https://kraft-sdlc.dev/guides/remote-access): reach the board from a phone or another machine.
- [Triggers](https://kraft-sdlc.dev/reference/triggers): start a chain from a schedule or an HTTP call.
- [Security](https://kraft-sdlc.dev/project/security): threat model; report vulnerabilities per [SECURITY.md](https://github.com/itsOmidKarami/kraft/blob/main/SECURITY.md).

Kraft Lite, a separate plugin, runs a chain inside a single
agent session with no service; see the [Kraft Lite guide](https://kraft-sdlc.dev/guides/kraft-lite).

## Project status

Kraft is young. Expect rough edges and frequent releases. Report bugs and ask
questions in [GitHub issues](https://github.com/itsOmidKarami/kraft/issues).
Report security problems as [SECURITY.md](https://github.com/itsOmidKarami/kraft/blob/main/SECURITY.md) describes.

## Contributing

Set-up, the `just` recipes, tests, and the pull request rules are in
[CONTRIBUTING.md](https://github.com/itsOmidKarami/kraft/blob/main/CONTRIBUTING.md). Releases are described in
[RELEASING.md](https://github.com/itsOmidKarami/kraft/blob/main/RELEASING.md). Everyone taking part follows the
[Code of Conduct](https://github.com/itsOmidKarami/kraft/blob/main/CODE_OF_CONDUCT.md).
