# VS Code extension

The extension in `vscode/` is a thin client over the daemon's HTTP API. What it
reads from the board and what ships with a release are contracts the daemon and
the release tooling keep for it.

## REQ board-carries-the-fields-the-extension-reads
The board payload SHALL carry every work item field listed in `vscode/contract/work-item-fields.json`.
enforced-by: tests/api/test_vscode_contract.py::test_the_board_carries_every_field_the_extension_reads
origin: vscode/contract/work-item-fields.json

## REQ release-stamps-the-extension-version
WHEN a release stamps its version, the system SHALL write it into `vscode/package.json` and leave every other key and the key order unchanged.
enforced-by: tests/test_stamp_plugin_versions.py::test_the_vscode_extension_is_stamped_and_keeps_its_own_shape
origin: dev/stamp_plugin_versions.py

## REQ session-never-sends-the-mcp-token
WHEN the extension holds a session for the URL in effect, the extension SHALL NOT send the local mcp-token on any request or on the live-updates socket.
enforced-by:
origin: vscode/src/core/connection.ts

## REQ sign-in-keys-sessions-by-normalized-url
WHEN the extension looks up or stores a session, the extension SHALL key it by the URL normalized to lower-case host, explicit port and no path.
enforced-by:
origin: vscode/src/core/serverUrl.ts

## REQ session-ended-prompts-sign-in
IF the server answers 401 to a request made with a session, THEN the extension SHALL delete that session and offer the sign-in command.
enforced-by:
origin: vscode/src/core/store.ts

## REQ signed-in-window-refuses-machine-bound-actions
WHILE a session is in use, the extension SHALL refuse the actions that read or act on the local machine and name the server in the refusal.
enforced-by:
origin: vscode/src/core/remote.ts
