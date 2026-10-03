### Breaking changes

- This release is 2.0.0, not 1.5.0: see **Upgrading from 1.4** under Highlights. (#489)

### New

- Kraft's plugin now installs on Cursor (`agent plugin marketplace add https://github.com/itsOmidKarami/kraft`, then `/plugins`) and on Antigravity (`agy plugin import` from a clone), with its skills and the `kraft` MCP server. Amp can add the skills with `amp skill add`. (#342)
- The install page and the agent-integration guide gain an Antigravity tab, and say that Gemini CLI works only with an API key or Gemini Code Assist since 2026-06-18. (#342)

- New `antigravity` harness runs agent tasks on Google's Antigravity CLI (`agy`), the replacement for Gemini CLI on individual Google accounts. It reads tokens, the conversation id for resume, and quota stops from its stream-json log. `prices.json` now includes Google's Gemini models, so a session launched with a base model id and `effort` gets a dollar estimate. (#344)

- Draft areas can rebase a stale draft onto the published files and read a component's YAML from a draft. (#361)

- **Kraft 1.5.0: a new web interface.** (#400)

Kraft's web UI is redesigned, at the same address. The board groups what needs you, what is running, what hasn't started and what is done, with a docked peek and bulk actions. The item page draws the chain, and each node, step and task opens in a side pane with its log, documents and config. A review page holds diffs, threads and gate decisions. Templates (Chains, Library, Harnesses, Repos) and Policy edit a draft that you review and publish. Phones get their own layout. The old interface is removed.

- **What moved.** Settings → Chains, Library, Harnesses and Repos are now under **Templates**. An item's Changes tab is its **Review** page. Documents open from the task that wrote them. The Timeline is the task log and the board's event list. Old addresses still work: `/settings/chains`, `/settings/notify`, `#node=` links and `/ng/...` bookmarks all redirect. Links from the CLI, notifications and the VS Code extension are unchanged. (#400)
- **Your theme.** On its first start, 1.5.0 rewrites `theme.yaml`. Your `palette` becomes `surface`, `accent` and `colour_amount`, and `palette` is removed. Your colours, mode and density stay as they were, and the old file is kept as `theme.yaml.pre-ux2`. A `palette` key is still read in 1.5; the next release refuses it. (#400)
- **Cancel keeps the branch.** Cancel stops an item and keeps its branch and worktree. To delete them too, use `kraft item abandon`. (#400)

### Fixes

- Approving a chain-revision gate from a review now works: the review submit takes the revision's digest. (#355)

- Fix: disconnecting a repository that still has running work items is refused with a 409 instead of stranding them. (#378)

- Fix: Pin httpx below 1.0, which the CLI's client does not support yet. (#409)

- Fixed: Raise cap on a budget stop that a policy `budget_usd` caused (desktop and the phone's Raise budget) now raises that policy in dollars and retries, instead of failing with a 409. A budget stop on the item's own cap still raises with Raise budget. (#418)
