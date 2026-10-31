# Screenshots: PR #1218, the gitlab-api backend

A failed item whose forge stop names an API account and where its token is read from (`facts.account: corp-gl`, `facts.source: env CORP_GL`). Before is `main` (faf47e34d), after is the branch (b8fc173; later commits change docs only). Mocked states from `frontend/e2e/contract/screens.ts`: `follow-ups/failed-forge-token` (desktop, 1280 px) and `phone/item-failed-forge-token` (phone, 390 px).

Before: the CLI login hint, plus raw `account` and `source` fact rows. After: the hint for an `env` source, with no fact rows for either.

| | Before | After |
|---|---|---|
| Desktop, light | ![](desktop-light-before.png) | ![](desktop-light-after.png) |
| Desktop, dark | ![](desktop-dark-before.png) | ![](desktop-dark-after.png) |
| Phone, light | ![](phone-light-before.png) | ![](phone-light-after.png) |
| Phone, dark | ![](phone-dark-before.png) | ![](phone-dark-after.png) |
