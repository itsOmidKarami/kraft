# kraft

Seven skills for driving [Kraft](https://github.com/itsOmidKarami/kraft) from an
agent session: connect a repo, spec and file work onto the board, read what is
running, act on the gates waiting on a person, report where a work item has
got to, and check a repo's Kraft config for drift
(`onboard`, `board`, `prepare`, `handoff`, `status`, `gates`, `check`).

These skills call Kraft's MCP server, so they need the `kraft` program itself:
`curl -fsSL https://raw.githubusercontent.com/itsOmidKarami/kraft/main/install.sh | sh`,
then `kraft admin init`.

## Install

    /plugin marketplace add itsOmidKarami/kraft
    /plugin install kraft@kraft

Published from `plugins/kraft/` in the Kraft repository, alongside
[kraft-lite](../kraft-lite). The version is Kraft's own release tag, so plugin
`0.6.0` is the surface `kraft 0.6.0` serves.
