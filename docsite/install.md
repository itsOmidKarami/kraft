# Install and run

```bash
uv tool install kraft-sdlc
kraft admin init   # register the MCP server and skills with your agent
kraft              # http://127.0.0.1:8765
```

No `uv`? The install script fetches the newest release and installs `uv` first
if you do not have it:

```bash
curl -fsSL https://raw.githubusercontent.com/itsOmidKarami/kraft/main/install.sh | sh
```

[`install.sh`](https://github.com/itsOmidKarami/kraft/blob/main/install.sh) is short
and worth reading before you pipe it to a shell. `kraft admin update` installs the
newest release later on, and `kraft --version` says what you have.

## Homebrew (macOS)

```bash
brew tap itsOmidKarami/kraft
brew install kraft
kraft admin init
kraft
```

`kraft admin update` detects a Homebrew install and runs `brew upgrade kraft`
instead of its usual `uv tool install`, so either update path works.

## From source (development)

```bash
just setup      # uv sync + npm install
just install    # build the SPA, install the `kraft` command
kraft           # http://127.0.0.1:8765
```

Releasing, and the labels a pull request needs:
[CONTRIBUTING.md](https://github.com/itsOmidKarami/kraft/blob/main/CONTRIBUTING.md).

## Where state lives

`$KRAFT_HOME` (default `~/.kraft`):

| | |
|---|---|
| `~/.kraft/run/` | `orchestrator.db`, `index.db`, `logs/`, `results/`, `worktrees/` |
| `~/.kraft/templates/` | the YAML the Settings screens edit — chain templates, `registry.yaml`, `policy.yaml`, `repos.yaml`, `access.yaml` |

`templates/` is seeded from the packaged defaults on first run and never overwritten
after, so an upgrade cannot clobber an edited policy. It is a plain directory of
files on purpose: the Settings screens are an editor for something you can diff,
revert, and `git init` yourself.

Binding off loopback requires a password — set one in Settings → Access while still
on `127.0.0.1`. The process refuses to start on a LAN address without one. See
[Remote access](remote-access.md) for the supported way to reach the board from
another device.

`KRAFT_HOME=~/kraft-other kraft` gives you a second, fully separate instance.

## Requirements

Python 3.14+, [uv](https://docs.astral.sh/uv/), Node 20+, git, and `claude` for
real runs (not needed for `just dev`).
[`bd`](https://github.com/gastownhall/beads) is optional: with it, every work
item gets a tracked bead, and without it Kraft files work anyway and says so.
Semantic search is opt-in: `just setup-vector` (downloads a ~130MB model on first
search); without it `/search` still works in FTS mode.
