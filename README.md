# Screenshots: PR #1222, Settings › Forge accounts

A new screen, so most shots are "after" only (rendered from the branch's UI-contract mocked states at cce4c555c). The Settings nav and the Workers dialog (which shares the dialog CSS this PR changed) have before (main) and after.

## Desktop, 1280 px

| | Light | Dark |
|---|---|---|
| List (`forge-accounts`, full page) | ![](desktop-list-light.png) | ![](desktop-list-dark.png) |
| Add dialog, fresh (`Name` focused) | ![](desktop-add-light.png) | ![](desktop-add-dark.png) |

| State | Shot |
|---|---|
| Empty (`forge-accounts-empty`) | ![](desktop-empty-light.png) |
| Add, api + paste (`forge-accounts-add-paste`) | ![](desktop-add-paste-light.png) |
| Edit an API account (`forge-accounts-edit-file`) | ![](desktop-edit-light.png) |
| Test result (`forge-accounts-test-result`) | ![](desktop-test-result-light.png) |
| Remove refused after undo (`forge-accounts-remove-refused`) | ![](desktop-remove-refused-light.png) |

## Phone, 390 px

| | Light | Dark |
|---|---|---|
| List (`forge-accounts`) | ![](phone-list-light.png) | ![](phone-list-dark.png) |

| State | Shot |
|---|---|
| Account menu with SSO (`forge-account-menu-sso`) | ![](phone-menu-light.png) |
| Paste a new token (`forge-account-paste`) | ![](phone-paste-light.png) |

## Before / after

| | Before (main) | After |
|---|---|---|
| Settings nav (new "Forge accounts" entry) | ![](desktop-settings-nav-before.png) | ![](desktop-settings-nav-after.png) |
| Workers "Add machine" dialog, 1280x900 | ![](workers-dialog-before.png) | ![](workers-dialog-after.png) |
| Workers dialog, 1280x600 (before: runs off screen; after: body scrolls, buttons stay) | ![](workers-dialog-short-before.png) | ![](workers-dialog-short-after.png) |
