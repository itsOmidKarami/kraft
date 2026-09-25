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
