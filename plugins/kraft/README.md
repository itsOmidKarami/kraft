# kraft

Seven skills for driving [Kraft](https://github.com/itsOmidKarami/kraft) from an
agent session: connect a repo, spec and file work onto the board, read what is
running, act on the gates waiting on a person, report where a work item has
got to, and check a repo's Kraft config for drift
(`onboard`, `board`, `prepare`, `handoff`, `status`, `gates`, `check`).

These skills call Kraft's MCP server, so they need the `kraft` program itself:
`curl -fsSL https://raw.githubusercontent.com/itsOmidKarami/kraft/main/install.sh | sh`.

## Install

    /plugin marketplace add itsOmidKarami/kraft
    /plugin install kraft@kraft

This plugin's manifest bundles `kraft admin mcp` as an `mcpServers` entry, so
installing it registers Kraft's MCP tools alongside the skills — no separate
`kraft admin init` needed, as long as `kraft` is already on `PATH`. Run
`kraft admin init` yourself only if you want the older, plain-file install
instead of a managed plugin, or a `.mcp.json` other tools can read straight
out of the repo.

Published from `plugins/kraft/` in the Kraft repository, alongside
[kraft-lite](../kraft-lite). The version is Kraft's own release tag, so plugin
`1.1.0` is the surface `kraft 1.1.0` serves.
