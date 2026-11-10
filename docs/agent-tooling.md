# Agent tooling

Everything here is optional. A plain git pull request needs none of it ([Contributing](../CONTRIBUTING.md#your-first-pull-request)). It is for people who work on this repository with a coding agent.

## Plugins and skills

`just setup-plugins` installs [superpowers](https://github.com/obra/superpowers)
and [impeccable](https://impeccable.style) for every agent this repo is worked
on with, and for Claude Code also
[ponytail](https://github.com/DietrichGebert/ponytail),
[techdocs-authoring](https://github.com/pkloehn1/techdocs-authoring) and the
engineering and design plugins from
[knowledge-work-plugins](https://github.com/anthropics/knowledge-work-plugins).
It is opt-in, and `just setup` never runs it, so a contributor's agents get
nothing they didn't ask for. It skips whatever is already
installed, and a missing tool or a failed download is a warning, not a failed
setup. To refresh them, run `claude plugin update <id>` and `npx skills update -p`:

- **Claude Code**, when `claude` is on your `PATH`: as plugins.
  `.claude/settings.json` turns them on, but Claude Code doesn't download a
  plugin from an outside source for you, so each clone installs them once.
- **Codex, Cursor, Gemini CLI, OpenCode, Amp, Antigravity and GitHub Copilot**,
  when `npx` is on your `PATH`: as skills in `.agents/skills/`, the folder all
  of them read. They are gitignored; beads is the only skill committed there.

A fresh git worktree has no gitignored files, so a worktree only gets the
`.agents/skills/` copies after `just setup-plugins` runs in it. For Kraft
workers, set this repo's `setup_command` in its `repos.yaml` entry to
`just setup setup-plugins && (cd vscode && npm ci)`, which is what
`kraft repo connect` proposes plus the plugins; Kraft runs it in every new
worktree. Claude Code's plugins live in `~/.claude` and need no setup per
worktree.

superpowers is off in Kraft workers: its skills ask a person questions and push
or merge a finished branch, and a worker does neither. ponytail is off
too: it puts a session into its own coding mode where a worker follows Kraft's
instructions. For Claude Code, `.claude/hooks/kraft-worker-plugins.sh` refuses
every superpowers and ponytail skill in a session with `$KRAFT_WORK_ITEM_ID` set
and tells the session and its sub-agents so.

The worker hook can't stop another plugin's own hooks. ponytail's still brief
each worker in its mode, and techdocs-authoring's turns back a worker's first
commit once, with a checklist for its message. Both plugins read an off switch
from the environment, so add them to `env:` in this repo's `repos.yaml` entry:

```yaml
env:
  PONYTAIL_DEFAULT_MODE: "off"
  TECHDOCS_SHORT_FORM_GATE: "off"
```

ponytail keeps its mode in one file per checkout, so an in-place item still
shares it with your own sessions there.

For the other agents, a setup Kraft runs copies only impeccable into the
worktree. An in-place item runs in your own checkout, so other agents there
still see the superpowers copies you installed.

Cloud agents start from a fresh clone, so give them the same step. Copilot's
coding agent already runs `just setup setup-plugins` (`.github/workflows/copilot-setup-steps.yml`).
For Codex cloud, Cursor background agents and the like, add this to the
environment's setup script:

```bash
for source in obra/superpowers pbakaus/impeccable; do
  npx -y skills@1.7.4 add "$source" --skill '*' --agent codex cursor gemini-cli opencode amp antigravity github-copilot --copy -y
done
```

Claude Code's cloud sessions (claude.ai/code, the Claude app) never install the
plugins a repository's settings turn on. Install them in the cloud environment's
setup script instead, which runs before Claude Code starts and covers every
session in that environment, whatever repositories it clones:

```bash
claude plugin marketplace add anthropics/claude-plugins-official
claude plugin install superpowers@claude-plugins-official
claude plugin install impeccable --marketplace pbakaus/impeccable
claude plugin install ponytail --marketplace DietrichGebert/ponytail
claude plugin install techdocs-authoring --marketplace pkloehn1/techdocs-authoring
claude plugin install engineering --marketplace anthropics/knowledge-work-plugins
claude plugin install design --marketplace anthropics/knowledge-work-plugins
```

## Beads and git hooks

If your checkout uses [beads](https://github.com/gastownhall/beads), its hooks and the pre-commit hooks share `.git/hooks/`.

git directory (`.git/hooks/`, shared by every linked worktree); there, append
this below beads' block in `.git/hooks/pre-commit` instead:

```sh
if command -v uv >/dev/null 2>&1; then
  uv run --no-sync pre-commit run || exit 1
fi
```

Keep `core.hooksPath` unset, or pointing outside the working tree:
`git config --get core.hooksPath` prints nothing. Don't run
`bd hooks install --beads` or `--shared`: they point it at `.beads/hooks/` or
`.beads-hooks/`, files a branch can change, so checking out someone else's
branch would run its hooks as you. `.beads/hooks/` is gitignored for the same
reason.

## The dot-directories

The dot-directories are mostly the maintainer's agent tooling. You can ignore
them unless you use the same tools:

| Directory | What it is | Do you need it? |
|---|---|---|
| `.github/` | CI workflows, issue and PR templates, CODEOWNERS | Yes: CI lives here |
| `.claude-plugin/` | the marketplace manifest that publishes `plugins/` to Claude Code and Codex | Only to rename a plugin or add one |
| `.cursor-plugin/` | the same marketplace for Cursor | Only to rename a plugin or add one |
| `.claude/` | Claude Code settings: a hook that blocks raw `pytest` (needs `jq`), the plugins it turns on, a hook that turns superpowers and ponytail off in Kraft workers, and the `release` and `steward` skills | Only with Claude Code |
| `.beads/` | beads config and git hooks for the maintainer's private tracker | No |
| `.agents/` | a beads skill for agent sessions; `just setup-plugins` adds superpowers and impeccable beside it, gitignored | Only with an agent other than Claude Code |
| `.codex/` | Codex hooks that call `bd`; they fail without beads installed | No |
| `.kraft-lite/` | this repo's Kraft Lite bindings (`bindings.yaml`) | No |
| `.kraft/` | `collection.json`, which publishes `config/` and `addons/` as the collection `kraft-sdlc` | Only to change what the collection lists |
| `.gitlab/` | the PR template for GitLab, from before the move to GitHub; a symlink to `.github/`'s | No |
