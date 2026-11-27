---
title: Web UI
navigation:
  title: Overview
description: Every screen of Kraft's web UI, from the board and an item's page to the review page, Templates and Settings.
---

Kraft's web UI is the server's own page: start `kraft` and open `http://127.0.0.1:8765`. You can do most of what the CLI does to a [work item](/concepts/vocabulary#work-item)
here too. Four verbs are CLI-only for a person: `kraft item abandon`,
`kraft item set-attachments`, `kraft item mr-label` and `kraft item set-chain`.

These pages say what each screen is for, what its less obvious controls do and what its states mean. To look
around before you install, open [the demo](/demo/): this web UI on sample
data, running in your browser with no server and no agents. Running and
waiting items move along their chains on their own, and stop at each gate for
you to answer. What you do there changes the items until you reload the page,
and nothing is saved.

The demo's corner badge, **About this demo**, opens five things to try, each
with a **Show me** that takes you to where it is done.

## In this section

- [Sidebar, search and keyboard](/reference/web-ui/sidebar-search-and-keyboard): the sidebar, search, vector search and the shortcuts every screen shares.
- [The board](/reference/web-ui/board): where every work item sits by what it needs from you, with a row's button, the filters and the side panel.
- [An item's page](/reference/web-ui/item-page): one work item in full, with the header's actions, the chain graph and the inspector.
- [Fix loops and test scopes](/reference/web-ui/fix-loops-and-test-scopes): a node's own view of a fix loop's rounds, and a changed-test-scope task's frame and chips.
- [Config before an item starts](/reference/web-ui/config-before-start): where each Config value comes from, and the draft that **Start** asks about.
- [The review page](/reference/web-ui/review-page): an item's diff with Compare, Viewed marks, line comments and the Finish review dialog.
- [Templates](/reference/web-ui/templates): the Chains and Library screens, and the marks on content that extends, comes from an add-on or is read-only.
- [Settings](/reference/web-ui/settings): each screen that edits Kraft's setup, and whether a change saves at once or waits for a restart.
- [Analytics](/reference/web-ui/analytics): the filters, the overview figures, the weekly merge chart and the node, repo and stop tables, with what each number counts.
- [On a phone](/reference/web-ui/on-a-phone): the phone layout, its bottom bar, the board's cards and the buttons on a stopped item.
