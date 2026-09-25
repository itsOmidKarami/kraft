# Kraft for VS Code

Clear what your Kraft agents are waiting on you for without leaving the editor.

- **Board** — every work item, grouped as in the web UI, with a badge for what needs you.
- **Gates** — a notification when a gate opens; open its document, approve or reject.
- **Review** — a work item's branch in the native diff editor; review findings show as diagnostics, and line comments are sent as one reject note or steer.
- **Config** — completion and diagnostics for Kraft's YAML config files, checked by the running daemon.

## Requirements

- A local Kraft daemon: `kraft admin start`.
- [Red Hat YAML](https://marketplace.visualstudio.com/items?itemName=redhat.vscode-yaml) for schema completion in config files.

## Settings

- `kraft.url` — daemon URL. Empty reads `$KRAFT_HOME/templates/access.yaml`.
- `kraft.notifications` — `all`, `gates` or `off`.
