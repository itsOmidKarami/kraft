# Changelog

Notable changes to the Kraft VS Code extension, newest first. The extension is
released together with Kraft, at the same version, so each section below is
Kraft's release notes for that version. The release workflow writes them; do not
edit this file by hand. Earlier releases are in Kraft's
[CHANGELOG](https://github.com/itsOmidKarami/kraft/blob/main/CHANGELOG.md).

## 2.2.0

### Highlights

- Chains can extend a chain: `extends` plus `remove`, `add` and `overrides`, so a shared chain can be customised without forking it. (#974)

### New

- A repository's `.kraft/` can build on an add-on's chain: list the add-on under `addons:` in `.kraft/library.yaml` and extend its chain by the add-on's own name. An add-on update that would break such a chain waits for a person instead of being refused. (#975)

- Edit a chain that extends another on the Templates screen: every change is an edit to its parent. Duplicate and Add to my library can extend a chain instead of copying it, and an extending chain's pane changes its base or detaches it. (#976)

### Fixes

- Fix: the sidebar's version and update badge stay on screen on a page taller than the window, such as Settings › Storage with many worktrees. (#970)

- Fix: on GitLab, the CI wait and the merge node now read merged results pipelines instead of waiting for one until they time out. (#973)

- Fix: Change base lists what it keeps and drops one row per entry, and refusals from Change base and from a new chain are shown as error toasts. (#977)

- The web UI and the console draw Kraft's default look, Ink with a violet accent at the full colour amount, from the start: the web UI no longer shows Graphite until the server answers, and the console's colours follow it. (#979)

## 2.1.0

### Highlights

- Add-ons and collections: share chains, library components, skills and agent profiles as an add-on in a git collection. `kraft admin addon` adds collections and installs, updates and removes add-ons; each is pinned to a commit, reviewed before every update, read-only in the Templates screens, and can update itself when the change cannot widen what a run reaches. See the new guide, Share chains with add-ons. A connected repo can share its own the same way, from `.kraft/` on its default branch: `kraft repo library` reviews and takes it. (#700)

- `kraft console`: a terminal UI for Kraft. The board, an item's document, history, log and diff, the server, repos and add-ons, in the web UI's words; every `kraft` verb also runs from its prompt. (#904)

- **In-place items.** A repo, or a single work item, can now run in the repo's own checkout instead of a per-item git worktree: `worktrees: false` in `repos.yaml`, `kraft repo connect --in-place`, or `kraft item create --in-place` / `--worktree`. Kraft runs one in-place item per checkout at a time, never forces, sweeps or deletes anything in it, and stops the item with a new `checkout` stop when something in the checkout needs you. (#953)

### New

- Feature: a start request (resume, retry, raise-budget, or create with autostart) made while every `max_concurrent` slot is busy now queues the work item with the new `queued` status and starts it, oldest first, when a slot frees, instead of answering 409. (#651)

- New: `kraft item create --after ID` makes a work item wait for other items. Started before they complete, it is `blocked`, and Kraft starts it once they have all completed; `kraft item unblock` drops the wait. (#653)

- A fix-loop step that runs several tasks in parallel can be selected on the node canvas: it has its own panel listing its tasks, and a link to it opens it. (#659)

- A blocked item now lists the items it is waiting on, on its page, in the board's side panel and on the phone, with **Unblock** to stop it waiting. The full new-item page can choose what a new item comes after. (#669)

- On the review page, ticking **Viewed** folds the file, arrows on each block of changes show the unchanged lines around it so you can comment on them, and the file tree shows each file's kind. (#671)

- On a phone, a node with a fix loop shows each of its rounds, the loop's repair and judge open like any task, a task's attempts are counted inside the round, and a test task lists the scopes it ran. (#682)

- On a phone, a test scope opens on its own screen, with its command, the paths that select it, how the other rounds treated it, and its log. (#683)

- A gate rejection gives the nodes it re-runs their fix-loop rounds back. Each time the chain runs a node again (after a rejection, a retry or a base change) is a pass: the desktop and the phone let you pick an earlier pass and read its rounds, tasks, logs and test scopes. (#688)

- Policy: `storage.worktrees.limit` and `quota` bound the disk worktrees use. Over the quota Kraft warns; over the limit it holds starts that would create a new worktree until space is freed, and says why in the queued answer, `/health`, doctor and a `work_item_storage_held` notification. (#701)

- Add `kraft view storage`, `kraft item archive` and `kraft item restore`, and `GET /api/storage` and `POST /api/storage/preview`, to see what the worktrees use and free it from a shell; a storage limit added by a reload or a Policy save now takes effect at once. (#704)

- Add: `storage.worktrees.auto_cleanup` in `policy.yaml` lets Kraft archive completed and abandoned items itself, oldest first and never one that ended within `min_age`, when worktree usage is over `storage.worktrees.limit`. Off unless set. (#708)

- Add a Storage page under Settings: worktree use against the quota and limit, the totals by category, and a clean-up of finished items that shows what it would free and what it would lose before it archives anything. (#710)

- Add: a connected repo can share chains, skills and agent profiles from `.kraft/` on its default branch. `kraft repo library` reviews and takes them, and new items in that repo run its `default` chain. (#726)

- Add: a repository's entry can set `library_auto_update: true` so its repo library takes text-only changes (prompts, messages, descriptions, and skills no gate reads) on the daily check without `kraft repo library`; anything else still waits. (#727)

- VS Code extension catches up with Kraft 2.0: the board shows the server's stops, actions and repo scope; review comments are the server's review threads; file work, watch worker logs, search, and act on many items at once without leaving the editor. The web UI also offers only the actions the server admits, and pre-2.0 work items no longer cause server errors. (#741)

- A repo library is now visible only to its own repository's work items, and every file Kraft reads from an add-on's or repo library's store is checked against its digest when it is read. A chain revision or an item draft can add a node that extends an add-on's component. A repository connected with submodules, or with a repo library, never gets an add-on's namespace as its id. (#756)

- A work item's files now live in one folder of its own, `run/items/<id>/`, outside its worktree, so nothing Kraft keeps for itself can reach a merge request. New work items get 8-character ids, and sessions get 6-character ids unique within their item. Outside its item, a session is written `<item>/<session>`. A work item can be named by the start of its id, 4 characters or more that no other item's id shares, wherever a command, MCP tool or API route takes one. Every existing id and file keeps working where it is. `kraft admin doctor` counts worktrees that still hold old `.engineering/` documents. (#767)

- The board, work item page and analytics were reworked from a design review: one primary action per item state, a failed card that says why and a finished card that shows the outcome, a chain graph that fits beside its pane, a "Waiting at each gate" view in analytics with the same filters on desktop and phone, stop kinds told apart without colour, a larger type scale (13px meta, 12px labels, 14px board titles), a gate review page that names the decision and says what Approve starts next, long chains folded into stages on the item graph, a keyboard-shortcuts dialog, and a phone gate card that opens its review before you approve. (#806)

- Every dashboard area outside the board, item page and analytics was reworked from a design review. These are the main changes: (#829)
- **Correctness:** five places where the UI said something false are fixed. (#829)
- **Type:** text sizes come from shared tokens. (#829)
- **Amber:** it now means only that a person is needed. (#829)
- **Disabled controls:** each says why. (#829)
- **Phone and desktop:** they use the same words, and new items start paused everywhere. (#829)
- **Config panes:** each is grouped and folds what is at its default. (#829)
- **Review:** you can approve with no comments. (#829)
- **Library:** it has an overview. (#829)
- **Repos:** the list says whether a repo can run work. (#829)
- **Harnesses:** each shows whether its CLI is installed. (#829)
- **Notifications:** browser permission comes first. (#829)
- **Policy:** sections show as tabs. (#829)
- **Auto-intake:** it validates repos and schedules. (#829)
- **Settings:** the pages share one frame. (#829)

- VS Code: gates arrive with the item's title, a coloured icon and a bell in the status bar; approving a gate that merges asks first on both routes; default keys for Next Gate, Approve, Reject and Submit Review; an Open submenu; titles instead of ids in every prompt and toast; screen-reader labels; and the gate opens as a rendered preview in a fresh window. (#865)

- Long chains are drawn as one chain: an overview of the whole chain sits over the full-size chain on the item page, the new-item page and the Chains editor. (#872)
- The web UI names nodes by what they are ("approve spec"), shows an offline banner on every page, titles each page, and has `?` shortcut help everywhere. (#872)
- The board filters by gate and stop reason, offers bulk Retry and Resume, and Analytics rows open the board filtered to them. (#872)
- Phone Chains edits drafts, like phone Library; code and board preferences move to their own Settings › Board & code page. (#872)
- Change review has one "Request changes" action that opens on writing the note. (#872)
- The docs site: exact error text and config keys lead in search, a generated Unreleased page on `/next/`, concepts that open with their model, guides with one golden path, and a shorter first-time path. (#872)

- New: a demo of the web UI on the docs site, on sample data in your browser, with no install, server or agents. Each release's docs link to the demo of that release. (#909)

- The item page's status cards fold to one line from their title, and start folded in a node's view. (#937)

- Diffs and code in review comments and gate documents are now coloured by Shiki in 60 languages, including Java, PHP, Python, Perl, Ruby and Jinja. (#944)

### Fixes

- Fix: the board's bulk Pause now includes items waiting on an agent rate limit. (#646)

- Fix: a worker session that ended in a merge conflict or an infrastructure stop is drawn as failed on the item's graph, not as done. (#647)

- Fix: a gate review verdict is now ignored unless the review session finished (`done` or `done_with_concerns`), so a session that was capped out, paused or errored can no longer approve or reject a gate. (#650)

- Fix: a fix loop with more than one repair task, such as the merge-request feedback loop's `repair` and `sync`, drew its first task off the loop on the node canvas. A loop step with several tasks now draws as one box that opens into its parallel tasks. (#656)

- Fix: an item's badge no longer goes stale after an abandon, a gate decision, an archive or an escalation turn, and a slow refresh can no longer erase an event that arrived while it was loading. (#657)

- Fix: the work brief names the checks that did not run and how many repair attempts were allowed, and no longer adds sections that only say there is nothing to report. (#658)

- Fix: `notify.yaml` refuses an event type it does not know instead of accepting a typo that never notifies. A file that already has one stops loading until the name is fixed. (#660)

- Fix: what a task flagged about its own work is shown again where a gate is decided: in the gate's panel, in `kraft view show`, and in the `get_work_item` tool. (#662)

- Fix: a malformed webhook URL such as `http://[x` is refused by Settings instead of answering 500. `kraft admin doctor` reports a `notify.yaml` that does not load or is enabled with no webhook URL. (#664)

- Fix: a node measured again no longer reads the findings, or the pending question, of a step that did not run this time. (#665)

- Fix: a queued start that fails now says so in the item's timeline instead of dropping out of the queue silently, and raise-budget, skip and escalate tell a queued or blocked item how to take it out. (#666)

- Fix: a queued or blocked item that is stopped or moved by something other than the queue now says so in its timeline, and pausing an item the queue is starting says it is starting instead of that it is paused. (#667)

- Fix: a shutdown that lands while Kraft is starting a queued item is no longer lost: the server stops, and the item goes back in the queue in its place for the next start. (#668)

- On the review page, a comment on an unchanged line stays under its line after a reload. (#673)

- On an item's page, a test-scopes frame for an item in one repository shows only its scopes, without the repository's number, name and status, and every such frame is now only as wide as its contents. (#680)

- Fix: on an item's page, a task's token count left out cached tokens, so a running Claude task could read "28". It now counts them and shows new and cached tokens apart, with every kind on hover. (#685)

- Fix: a task, step or node skipped while it waits on CI or a review no longer stays "waiting" on the item page; a skipped task now reads "skipped". (#686)

- Fix: a node's or item's token total now says when its output count is missing a session's, as `at least N out` or `out not known`, instead of presenting the sum as complete. (#689)

- Fix: a waiting item's row on the board now names its node ("verification · next check in 1m") instead of showing only the time. (#690)

- A test task reads failed when one of its scopes failed, even if a later one passed. On the desktop, a scope that a round ran more than once offers its earlier runs, and a one-repository item's test task no longer counts or names its repository. A scope link that matches nothing says why. (#697)

- On the desktop, the fix-loop round and the pass a node shows are in the page's address, so a link opens on them, as it does on a phone. (#699)

- A pass that a gate's reviewer started by fixing the work itself says "after a fix at …", not "after a reject at …". (#702)

- Fix: on the phone, a blocked item's card links each item it waits on, and its text says that Unblock starts the item without them. (#705)

- Fix: a work item waiting on another item before its first node is listed under Not started, not Running, on the board and in the VS Code extension. (#706)

- Fix: the MCP gate and lifecycle tools (`approve_gate`, `reject_gate`, `resume_work_item`, `retry_work_item`, `raise_budget`, `skip_work_item`, `complete_work_item`, `cancel_work_item`) no longer return the item's frozen chain, saving about 25k tokens of agent context per call. (#712)

- Fix: a finished item's card on the board shows its whole chain instead of a shortened one. (#713)

- Fix: a work item whose GitHub pull request conflicts with its base branch is rebased at once, instead of waiting for CI that GitHub never starts until the wait times out. (#717)

- Fix: a pause, skip or cancel no longer waits out the kill grace when a process was forked at the moment of the stop. (#719)

- The docs now live at https://kraft-sdlc.dev. (#730)

- Fix: when a restart from VS Code doesn't happen because Kraft is still running (you answered no to the running-agents question), the notification says Kraft is still running instead of offering Start Kraft. (#747)

- Build(deps-dev): Bump vite from 8.3.2 to 8.3.4 in /frontend (#775)

- Build(deps-dev): Bump @playwright/test from 1.63.0 to 1.64.0 in /frontend (#778)

- Build(deps-dev): Bump @vitejs/plugin-react from 6.1.1 to 6.1.2 in /frontend (#781)

- Build(deps): Bump fastembed from 0.8.1 to 0.9.0 (#782)

- Build(deps-dev): Bump ruff from 0.16.10 to 0.17.0 (#785)

- Build(deps): Bump pydantic from 2.13.5 to 2.14.1 (#786)

- Build(deps): Bump idna from 3.19 to 3.20 (#787)

- Build(deps): Bump lucide-react from 1.51.0 to 1.55.0 in /frontend (#788)

- Build(deps-dev): Bump @types/node from 26.6.4 to 26.6.5 in /frontend (#789)

- Build(deps): Bump fastapi from 0.142.2 to 0.143.0 (#790)

- Dashboard polish: menus and bars lift off the page in the light theme, backdrops dim, keyboard focus rings are no longer cut off and focus returns after in-place edits, long names and paths fit on the review page and phone, phone Chains show node names, and every write refuses while Kraft is offline. (#891)

- Dashboard consistency: buttons, link buttons, selects, empty states and section heads look and behave the same everywhere; phone and desktop word the same things identically; icon sizes and corner radii follow one scale; the sidebar and switches respect reduced motion. (#902)

- Chips and badges look the same across the dashboard, desktop and phone: read-only labels are square-cornered badges in one set of tones, and things you press are round chips that fill when on. (#907)

- Fix: `server.log` holds each log record on one line, so `grep` finds whole records; the terminal keeps its rendering. (#914)

- Gates read Review everywhere and a queued item offers Remove from queue; the board list is one Tab stop with j/k, g and a keys and rows lead with their reason; Appearance › Density works; a bad payload shows a reload message instead of a blank page; approving a gate no longer stalls when the semantic-search model cannot be downloaded; accessibility fixes in the Library, task panel, phone sign-in, review ranges and light-mode chips; the docs site keeps readers on its own origin and its LLM text renders diagrams. (#916)

- Fix: the chain canvas keeps the chain in view (a mouse wheel scrolls along a chain instead of off it) and a long chain's canvas fills the pane; Settings › Harnesses fills a wide screen instead of floating in the middle. (#919)

- Resume, retry, gate approval and `kraft admin stop` handle a slow or hung git checkout and rebase without leaving files, processes or a stuck item behind. `kraft item abandon` asks why and records it on the item and its bead, cancel closes the item's bead too, and saving repos, policy and intake settings keeps your comments and layout. (#928)

- Fix: after a retry, an item's history no longer shows "run forked" and "cap counters reset", and a gate the retry reopened, or a steer that never reached an agent, now reads in words (web item page and console). (#930)

- Fix: the node canvas no longer shows an "on failure" line on a pass where the on-failure handler never ran. (#935)

- Fix: an item page's Recent no longer says a session "is running" after a pause, cancel, skip or Stop escalation ended it; a stopped escalation turn reads "escalation stopped". (#938)

- Fix: an item's history no longer shows a raw event name for a cancel, an abandon, a Mark complete, a skipped task or step, or a session stopped, lost or resumed. (#941)

- Fix: pausing, cancelling, skipping or stopping an escalation in the instant an agent was starting no longer leaves that agent running or writes a "session started" event after the pause. (#942)

- Fix: a merge request whose description lacks a section CI requires is now repaired in the item's description document and its failed checks re-run, instead of stopping the item. (#946)

- Fix: a merge request stays a draft through CI repairs until the chain's `mark_ready` step, so the final gate comes before publication. A custom chain that merges without a `mr.mark_ready` step must now add one. (#948)

- Fix: `kraft repo library` prints its review on separate lines instead of one line with literal `\n`. (#960)

- Fix: a start held by the storage limit says so, with usage and how to free space, instead of "N of M slots are busy"; `kraft view show` names an in-place item, the item holding its checkout, and a `checkout` stop. (#962)

- Fix: in `kraft console`, an item paused from the Board offers `p` to resume it, instead of no action until it is reopened. (#963)

- Fix: `kraft item create --autostart` for an in-place item that waits for the checkout says which item is using it, instead of "N of M slots are busy". (#965)

- Fix: the worktree storage limit counts a worktree a start just created at once, so a burst of starts released together can no longer pass the limit several times over. (#966)

- Fix: `kraft view docs` lists the spec or plan a pending gate is about, not only after the next node runs. (#967)

- Fix: a gate is named by what it decides everywhere: the default chain's last two gates read "approve local review" and "approve final review" on the web UI, phone and console, as in VS Code, instead of naming the brief. (#968)

## 2.0.0

### Highlights

- **Kraft 2.0: a place to work, not just an engine.** Kraft 1.0 shipped the engine: chains that take your coding agent from spec to merge request and stop only where a decision is yours. 2.0 is built around those decisions. Since 1.4, more than 180 pull requests have changed over 1,500 files, and most of that is what you see and touch. (#400)

  - **A new interface, built around what needs you.** Open Kraft at the same address and you get the redesigned interface; the old one is gone. The board keeps 1.4's four groups and its one button per row, you can act on several items at once, and an item's page draws its chain. ⌘K (Ctrl+K) search and the phone layout are redesigned to match.
  - **Review the way you would on a code host.** Each item has a **Review** page with the diff, the comment threads and the gate decision. Comment on a line or a range, suggest a change in place, and send a whole round of comments at once with **Finish review**.
  - **Shape the work from its page.** Read the spec and plan an item was filed with, set the item's and its nodes' model, effort and limits beside the values its chain would use, change a running item's chain, and raise the cap or budget that stopped it. Chains, the library, harnesses, repos and policy are edited as a draft that you review, then publish.
  - **Connect a repo in one step.** `kraft repo connect` proposes the setup and test commands from the repo's own task runner, its toolchain's files and its CI, explains each choice, and rehearses them with `--verify`.
  - **Safer to share.** No other site can frame the board, an image from another site in a document or review thread loads only when you click it, and off loopback Kraft answers a browser only at a name you listed.

  **Why 2.0 so soon after 1.0?** Because Kraft keeps its stability promise. Making the interface and the API consistent changed some `--json` fields and exit codes, and the database moves to a schema 1.4 can't open. A minor version would have hidden that, so this is a major one.

  **Upgrading from 1.4?** **Breaking changes** below lists what to check, and [Upgrading from 1.4](https://itsomidkarami.github.io/kraft/guides/run/upgrade-from-1-4#update-from-14) lists every change and how to move without ending a running agent.

  **On a 1.5.0 release candidate?** Those candidates were 2.0's pre-releases; no 1.5.0 was released. On 1.5.0rc14, `kraft admin update` follows the rc channel and keeps `[vector]` and your Python. On rc13 it follows it too but drops both: use the `uv tool install` command in [Upgrading from 1.4](https://itsomidkarami.github.io/kraft/guides/run/upgrade-from-1-4#update-from-14), as on any earlier candidate. If a candidate already converted your `theme.yaml`, the original is `theme.yaml.pre-1.5` or `theme.yaml.pre-ux2`, not `theme.yaml.pre-2.0`. The VS Code extension on the Marketplace stays at 1.4 until 2.0.0 and is read-only against a 2.0 release candidate: install the `kraft-<version>.vsix` attached to the candidate's release with `code --install-extension`. See [Upgrading from a 1.5.0 release candidate](https://itsomidkarami.github.io/kraft/guides/run/upgrade-from-0-x-or-a-release-candidate#upgrading-from-a-150-release-candidate).

  **Looking ahead.** The sandbox backend that has run tasks under Docker and Podman since 1.2, and the Docker Sandbox Kits added in 1.4, are what richer sandboxes and remote workers are meant to build on during 2.x.

### Breaking changes

- Updating from 1.4 changes some things a script or a habit may rely on. These are the ones to check first; [Upgrading from 1.4](https://itsomidkarami.github.io/kraft/get-started/install#upgrading-from-14) lists every change and how to move. (#489)
  - **The database upgrade is one-way.** The first start of 2.0 upgrades `orchestrator.db` to a schema 1.4 can't open; 1.4's `run/logs/server.log` then says "database schema v52 is newer than code v44". Back it up first. 2.0 also keeps a copy as `orchestrator.db.pre-v52-<time>`.
  - **Update with your install's own tool, not with 1.4's `kraft admin update`.** On a uv or `install.sh` install it installs 2.0 without `[vector]` and on uv's default Python, and on a pipx install it adds a second Kraft with uv. A 1.4 server left running then serves 2.0's interface and misreads its board until you run `kraft admin restart`. On Homebrew, `brew upgrade kraft` then `kraft admin restart` does it, and 1.4's update runs the same command.
  - **`--json` and API shapes changed.** `agent_overrides` is an object, not JSON text (and so are `node_overrides` and `policy_override` in action answers); `budget_cap.cap_usd` is the cap that stops the item first; `kraft view events -f --json` prints one object per line. `kraft item approve`, `reject`, `pause`, `resume`, `retry`, `raise-budget`, `skip`, `complete`, `cancel`, `escalate`, `set-chain`, `resolve` and `reopen`, and `kraft item review approve` or `request-changes` at a pending gate, print one line now; add `--json` for the fields.
  - **Exit codes and refusals changed.** `kraft admin restart` with no server running exits 1. `kraft repo disconnect` is refused (409) while the repo has open items. A work item title must be one line of plain text, not blank (422; the right-to-left and left-to-right marks in pasted Persian, Arabic and Hebrew text are allowed). A model override that isn't a model id, a review comment on a path outside the repository, an escalation of an item a spend cap stopped, and a cap raised to what the item has spent are refused where 1.4 stored or started them.
  - **Host checks are stricter.** A tunnel or reverse proxy in front of a loopback-bound Kraft now gets 403, and a board opened from another device needs its name in `allowed_hosts` (wildcards aren't supported).
  - **Some behaviour changed.** Cancel keeps the item's branch and worktree (`kraft item abandon ID --yes` deletes them). A merge request closed without merging stops its item. `test_command: ""` means no tests. A repo entry's `submodules:` list is no longer read. Connecting a connected repo again saves what it left undecided. A schedule's day of week `7` fires on Sundays.
  - **The VS Code extension 2.0 needs Kraft 2.0.** VS Code updates it on its own; against a 1.4 server it shows the board and disables every action. Update Kraft too.

- **The config directory is `$KRAFT_HOME/config/`.** The first start renames 1.4's `templates/` to it, every file as it was, and leaves `templates` as a link so a 1.4 process still running finds its files. A `templates` that is itself a link to another directory (a dotfiles repo, say) is not moved or read: set `KRAFT_CONFIG_DIR` to its target, which the start and `kraft admin doctor` name. `KRAFT_CONFIG_DIR` names the directory and `KRAFT_TEMPLATES_DIR` is still read when it is unset, but if it names the old `templates/` and you also made a `config/`, 2.0 refuses to start until you merge `templates/` into `config/` and remove it. (#501)
- **Keys moved between files.** `policy.yaml`'s `triggers:` are now `schedules:` in `intake.yaml`, and an `intake.yaml` `max_concurrent` moves to `policy.yaml` unless it already sets one. The first start moves both and leaves the two files readable by you alone (`0600`). (#501)
- **A chain is a chain, not a template.** A repo entry's `default_chain_template` is `default_chain`, `kraft item set-chain` takes `--chain`, and `create_work_item` takes `chain`. The MCP tool `set_chain_template` is now `set_chain`: an agent prompt or skill that names the old tool needs the new one. Every other old name is still read, and `kraft admin doctor`'s `moved keys` row names any a home still has. `kraft repo list --json`, `kraft repo connect --json`, the MCP tool `ensure_repo` and `GET /api/repos` answer `default_chain`. (#501)
- **Your theme is converted once.** The first start rewrites `theme.yaml`: `palette` becomes `surface`, `accent` and `colour_amount`, which draw the same colours. The original is kept as `theme.yaml.pre-2.0`. (#501)

- A sandbox with no `network:` policy is now refused for every harness; add a `network:` policy, or set `unrestricted_network: true` to run it with open egress and gates not enforced (Claude, and Codex or Cursor with a tool policy, are still refused). (#639)

### New

- Kraft's plugin now installs on Cursor (`agent plugin marketplace add https://github.com/itsOmidKarami/kraft`, then `/plugins`) and on Antigravity (`agy plugin import` from a clone), with its skills and the `kraft` MCP server. Amp can add the skills with `amp skill add`. The install page and the agent-integration guide gain an Antigravity tab, and say that Gemini CLI works only with an API key or Gemini Code Assist since 2026-06-18. (#342)

- New `antigravity` harness runs agent tasks on Google's Antigravity CLI (`agy`), the replacement for Gemini CLI on individual Google accounts. It reads tokens, the conversation id for resume, and quota stops from its stream-json log. `prices.json` now includes Google's Gemini models, so a session launched with a base model id and `effort` gets a dollar estimate. (#344)

- Chains take three new optional keys: `description` on a chain, shown in the chain list; `icon` on nodes, steps and tasks, drawn on the board; and `artifact_required` on a gate, which refuses approval while the gate's document is missing. (#347)

- Every stopped item now says why. `kraft view show`, the API and the MCP tools report its stop: the kind (a question, a cap, a budget, a failure, a merge conflict, a closed merge request and so on), the facts behind it, and when it resumes, plus the status the board shows for it, which now includes Failed and Waiting. An item stopped on a merge request closed without merging can be picked up again: `POST /api/work-items/{id}/reopen-mr` reopens the merge request and retries the node. New API routes duplicate an item; pause, cancel, archive or restore several at once; preview what a cancel keeps and close the merge request with it; report today's spend; and dry-run filing an item. (#348)

- `kraft view diff` and `kraft view compare` take `-w` (`--ignore-whitespace`), and so does the `compare_changes` MCP tool. The review API also remembers which files you marked viewed, and says which node a rejection restarts from (`GET /api/work-items/{id}/fix-target`). (#349)

- `kraft admin reload` (and `POST /api/templates/reload`) now also rereads `intake.yaml`. (#362)

- `GET /api/health` reports `uptime_s`, the seconds since the server started. (#406)

- `kraft repo connect` proposes setup and test commands for any repo: from its own task runner (just, make, Taskfile, mise, scripts), its toolchain's lockfile (40 detectors across 17 language ecosystems and build systems), or its CI. It explains each choice, takes `--test-command`/`--setup-command`/`--no-tests` (`--no-tests` saves the repo enabled; the `ensure_repo` MCP tool takes `test_command` and `setup_command` only, and `test_command: ""` saves the repo disabled for a person to enable), rehearses the result with `--verify`, and learns your conventions from `detectors.yaml`. A self-hosted GitLab or GitHub remote is recognised by its host name. `test_command: ""` now means the repo has no tests, so its test step passes where it used to stop; see **Breaking changes** for an entry that already has it. (#445)

- The review page's line comments work like GitLab's: the composer reads "Comment on lines −2 to +2"; dragging the handles on the shaded range, or Shift-clicking, changes its lines; a hint says how to pick several; and a range can run from a removed line to its replacement. (#474)

- `kraft item comment` takes `--start-side` for a range across sides, and MCP `add_review_comment` takes `start_side` and `quote`. A comment sent without a quote is quoted from the diff, so the agent gets the lines, as for one made on the review page. A suggestion on an old-side range is refused, and so is a file path outside the repository. (#488)
- Review threads carry their side wherever they are named: `calc.py:+8`, `calc.py:-3 to -4`, `calc.py:-2 to +2`, in `kraft view threads` and in the agent's note. (#488)
- `kraft view docs ID --attachment spec|plan` (MCP: `get_attachment`) prints the spec or plan an item was filed with, through the pager unless `--no-pager` is given, as `view doc` does. (#488)
- `kraft view show` on a budget stop the item can raise names `kraft item raise-budget ID --usd N`. (#488)

- **Open in editor** offers only the editors found on the server, and a new **Default editor** setting (`editor` in `theme.yaml`) picks one for every browser. 1.4 remembered the last editor in each browser. (#502)
- In ⌘K, an item waiting at a gate opens its review instead of approving the gate in place, as 1.4's palette did. (#502)
- A document opened from search highlights the matches; ↑ and ↓ step through them. (#502)

- Schedules take ranges and steps: each cron field is `*`, a number, a range (`1-5`), a step (`*/15`, `10-40/10`, `5/20`) or a list of those, and each value is checked against its field. A schedule Kraft can't run is named on its field in **Settings › Auto-intake**, `kraft admin reload` refuses the file (exit 1), and `/api/health` reads degraded with `invalid_intake`; the other schedules keep firing. 1.4 accepted only `*` and lists of numbers. (#517)
- A title with a lone surrogate is refused (422) like other control characters, and `kraft view show` no longer prints a stored title's escape sequences raw. (#517)

- A work item's spend line shows its running time against its time cap ("$2.41 of $5.00 · 1h 12m of 8h"), and **Settings › Auto-intake** says when the next check is. (#518)
- API: `GET /api/repos` adds `suggested`, the git checkout the server was started in when it isn't connected yet; `GET /api/work-items/{id}` adds `running_time` (`{running_s, cap_minutes}`). (#518)

- A gate's document opens in your editor from its viewer, as other documents do; 1.4 offered no **Open in editor** or **Copy path** for a gate's artifact. (#523)

- Item pages now say whether each fix-loop round committed a change. (#524)

- Item page: the gate pane and failed card show the verification's test result, and a failed infrastructure stop names its cause (a bad forge token points at Repos, a stranded item says Retry). The API gains `test_result` and `facts.cause`. (#525)

- The task pane shows how far the implementing task is through its plan's sub-tasks, with each one's commit, and picks an attempt from a menu in its subtitle. (#533)

- Node view: a fix loop's repair and judge now sit on its arc with a round selector, and `test_changed_scopes` opens into its repositories and scopes. (#536)

- New: a task's **Output** leads with the document it wrote, such as the work brief, and `GET /api/work-items/{id}/artifacts/{kind}` (with `POST …/open`) reads it, whether or not a gate is pending on it. (#540)

### Fixes

- Approving a chain-revision gate from a review now works: the review submit takes the revision's digest. (#355)

- A resumed Cursor or Amp session now records the tokens it spent; before, they were reduced by the earlier session's spend, often to zero. The `mcp server` row in `kraft admin doctor` now says it checks Kraft's registration with Claude Code only, and names the agents it does not check. (#359)

- A gate's `auto_review` can extend a library task (`extends:`). Before, the chain failed to load with `auto_review.kind: Field required`. (#379)

- If an item's worktree was removed from disk, `kraft view diff`, `kraft view compare` and `kraft repo open` now say "worktree was removed from disk" instead of "no worktree yet". Retry recreates it. (#385)

- Kraft now requires httpx below 1.0. httpx 1.0, so far a pre-release, drops the client the `kraft` command uses, and an install that picked it up failed on every command. (#409)

- The `kraft` MCP server now reports its version to the agent that connects; it was empty. (#416)

- Kraft now checks the hostname of every request on a loopback bind, and of every browser request on a non-loopback bind, so a web page can no longer reach the board under an unlisted name. (#419)

- `kraft admin doctor` now fails when a chain would launch a Claude worker that nothing registers Kraft's MCP server for, even with a repo connected. It also fails when the `kraft` command isn't on your `PATH`. Before, it could pass while every Claude worker would be refused. (#421)
- The API docs at `/docs` and `/openapi.json` now name Kraft and its version instead of "FastAPI 0.1.0". (#421)
- When `kraft admin start` refuses a network bind because no password is set, it now says how to set one. (#421)

- The Cursor permission hook's note in `.git/info/exclude` no longer carries an internal tracker id; an existing note is rewritten in place. (#433)
- `kraft admin update` and the server's refusals call the old template format "Kraft 0.x template configuration" instead of "pre-V1". (#433)
- `kraft repo connect` says when it found no setup command, and how to declare one. (#433)
- `install.sh` reports the installed version even when uv's bin directory isn't on PATH yet, and says to run `uv tool update-shell`. (#433)
- `kraft admin doctor`'s `agent:` row names the chains a missing agent CLI stops, instead of always saying no chain can run. (#433)
- `kraft admin templates library NAME` answers an ambiguous bare name with a 409 listing each full id, instead of a 404. (#433)
- Clearer `--help` for `kraft item raise-budget`, `kraft item create`'s title and `kraft admin permission-hook`. (#433)

- VS Code extension: updates its bundled `ws`. (#436)

- `kraft repo connect` proposes `uv sync` and `uv run pytest` only for a repo with a `uv.lock`. A repo with a `pyproject.toml` and no lockfile connects disabled until you set its test command; `kraft repo connect` says how. A `uv.lock` or `Cargo.lock` that the setup command writes in a repo that commits none is no longer committed by every work item, and the item's events say it was left out. A lockfile the agent writes, or its edit to the one the setup wrote, is still committed. (#442)

- A board opened over the network at a name in `allowed_hosts` now gets live updates. Its event connection was refused, so it showed "reconnecting…" and never updated on its own. (#447)

- A failed `kraft admin start --detach` (and `restart`, `update --restart`) names the error that stopped the server, such as `database schema vN is newer than code vM`, instead of only the end of a traceback that could cut it off. (#449)
- `kraft admin restart` and `kraft admin update --restart` list the active items whose agents a restart would end and ask before going on (`-y` skips the question); `kraft admin stop` lists them. Their help and the upgrade docs say to pause running items first. (#449)

- MCP tools now tell the agent why a call was refused (a 404, a 409, or the worker self-action guard), instead of only "Error executing tool". (#450)
- The board no longer shows a skipped gate, or the gate of an item that ended, as pending, on load or live. (#450)
- CLI errors start with a single `kraft: ` instead of `kraft: kraft 404:`. (#450)
- Piping a `kraft` command into `head` no longer ends in a BrokenPipeError traceback. (#450)

- Archiving an item, by hand or automatically, no longer deletes a branch with commits that were never pushed. It keeps the branch and says how many commits it kept. (#451)
- `kraft view list --include-abandoned`, `--status abandoned` and the MCP `list_work_items(status="abandoned")` now list abandoned and cancelled items. (#451)
- Archiving keeps commits made on a detached HEAD in the item's worktree, on a new `kraft/rescued/<id>` branch. (#451)

- A sandboxed task no longer fails with "Permission denied" when rootless Podman or Docker answers Kraft's first `info` slowly: Kraft asks the runtime again instead of assuming it is rootful, and refuses the launch if there is still no answer, rather than run it as container root. A runtime whose daemon is down is named as such, not reported as slow to answer. (#452)

- A monorepo's nested test scopes now name their own directory: `kraft repo connect` writes them as `sh -c 'cd frontend && npm test'`, `sh -c 'cd backend && uv run pytest'` and the like. Before, a nested scope's bare command ran from the repository root and could not find its project. Connecting again doesn't rewrite scopes a repo already has: if 1.4 saved them, give each its directory in **Settings › Repos**, or in its `repos.yaml` entry. (#453)

- The board and API can no longer be framed by another site. Every response sends `Content-Security-Policy: frame-ancestors 'self'` and `X-Frame-Options: SAMEORIGIN`, which closes a clickjacking hole on the no-login loopback board. (#455)
- An image from another site in a document, review thread or gate page now shows as a link (`image: host/path`) and loads only when clicked, so a worker can no longer get data out through your browser. Same-origin and inline `data:` images still show. (#455)
- An `allowed_hosts` entry saved with a port, capitals, a scheme or a trailing dot now matches. Kraft stores the bare lowercase name, maps a Unicode name the way your browser does, and refuses a new wildcard or anything else that is not one host, with a message naming the entry. An entry like that already in `access.yaml` no longer blocks saving the list, and `kraft admin doctor` names it. (#455)
- `/docs` and `/redoc` load Swagger UI and ReDoc pinned to exact releases with Subresource Integrity hashes, instead of the newest 5.x and 2.x from the CDN, and no longer fetch their icon from FastAPI's site. (#455)
- `kraft admin start --host ::1` no longer crashes with an invalid-URL error. IPv6 addresses are bracketed wherever Kraft builds a URL from its bind, including the restart address on the Access screen. (#455)

- The wheel and the VS Code extension now ship `THIRD_PARTY_LICENSES.txt`, with the license of every open-source package they bundle (react, lucide-react, the Inter font, ws and the rest). (#461)
- `install.sh` now stops with a clear message when it can't download or run the uv installer, instead of failing later with `uv: not found`. (#461)

- Archiving or abandoning an item whose repository was moved or deleted no longer fails with a 500: its worktree is removed and the answer names the missing path (`repo_missing`), and one such item no longer stops the hourly auto-archive. (#462)
- `kraft item raise-budget` (and MCP `raise_budget`) now raises a stop on the item-wide `budget_usd` of the item's policy too, keeping the policy's other fields, and changes nothing when every slot is busy; the hints that still point at `set-policy` warn that it replaces the whole override. (#462)
- A browser opening a network-bound board at a name not in `allowed_hosts` now gets a page naming that name and how to allow it, and `kraft admin doctor` warns about a network bind with no `allowed_hosts`. (#462)

- Security: the Cursor permission hook no longer imports Python modules from the worktree or from a `PYTHONPATH` in the worker's environment, so a planted file can no longer answer it in Kraft's place. The server adds this to the hook entry in existing worktrees when it starts. `kraft admin start --detach` and `restart` now work from a directory holding a file such as `mcp.py`. (#463)
- Security: search no longer indexes a file reached through a symlink in a repository's `.engineering/` folder, and indexes a worker's session summary only from its own worktree and only as Markdown. So neither a repository nor a worker can point search at a file such as a token outside the repo or your checkout's `.env`. (#463)
- `kraft` and the MCP tools now encode a work item, thread or document id in the request path, so a malformed id gets a not-found error instead of reaching another route. (#463)
- `/docs` and `/redoc` no longer load ReDoc's logo from a third-party site. (#463)

- `kraft view diff` now titles the committed changes "the change under review" and the working tree "uncommitted", so an empty working tree no longer looks like an empty review. (#464)
- `kraft admin update` on a release-candidate, beta or alpha install follows its own channel instead of reporting the newest stable release. (#464)
- The missing-setup-command stop, the doctor `setup` row and `kraft repo connect` now also point to the **No setup needed** checkbox under Settings › Repos. (#464)

- A repository whose `.gitmodules` is crafted to be slow to parse no longer freezes the Kraft server when you connect it or an agent calls `ensure_repo`. (#473)
- The permission gate denies a Cursor worker's direct Write or Delete of `.cursor/hooks.json` (in any case, or through a symlink), and a Shell call whose text names `.cursor` or `hooks.json`, where Kraft's permission hook sits. A script it runs can still reach the file; each launch with policy writes the entry back. (#473)
- A new home's config files are readable by you alone (`0600`). (#473)
- `install.sh` runs nothing if its download is cut short. (#473)

- A `.venv` the repo does not ignore stays out of a work item's commits, even with `status.showUntrackedFiles=all`, and `kraft repo connect --verify` warns about it. (#482)
- Filing a work item on a repo with no setup or test command warns that the item will stop, and `kraft item create --autostart` says why it filed the item paused. (#482)

- A 422 for a request body the API can't read now names each field and what is wrong with it, in `kraft` commands and MCP results, instead of a Python list. (#483)
- The permission hook's error line reads `kraft permission-hook: unavailable: 404: unknown session`, without a second `kraft` lead. (#483)
- When a gate's review agent leaves the decision to a person, its `gate_auto_review_skipped` event now gives the reason in a `note` field, shown in `kraft view events`. (#483)

- `kraft admin update` keeps the `vector` extra and the Python you chose, on the minor version Kraft runs on, so `uv python upgrade` carries it along. It installs from PyPI and pins the release it chose, so `uv tool upgrade` leaves it as it is. On a pipx, pip or source install it prints the command that updates that install, instead of adding a second copy. (#484)
- `kraft admin health` shows the version the server runs. From 2.0 on, after an update without `--restart`, health, doctor and the web UI say a restart is pending and the server is still running the old version, and the old server no longer serves the new interface. (Updating from 1.4 runs 1.4's code: see **Breaking changes**.) (#484)
- `kraft admin stop` and `restart` no longer signal an unrelated process left in a stale `kraft.pid`, even one whose command line mentions "kraft", and a stale pidfile no longer keeps Kraft from starting. (#484)
- Search falls back to text search, and says why, when the vector model cannot be loaded, instead of failing. (#484)
- A config directory you create before the first start no longer stops Kraft from seeding its defaults. (#484)
- `kraft-sdlc` requires `pyyaml>=6.0.1`, which installs on Python 3.12, and its release wheel is reproducible from the tag. (#484)

- Kraft rebases a work item onto its base branch before opening its merge request, and on resume and retry, even when the worktree holds untracked files such as Kraft's own session notes. An untracked file the base would overwrite stops the item and names the file. (#486)
- `kraft admin stop` ends a setup command that is still running. (#486)
- `kraft admin doctor` stops reporting orphaned agent sessions once their items are retried or end. (#486)
- A budget stop names the cap as it was set, and `kraft view show` prints dollars the same way. (#486)

- A rebase cut short by its time cap or a server restart no longer lets the item carry on without its commits. Retry and resume stop for a person and say how to get the branch back, and a hung smudge filter or hook is killed along with the rebase it held up. (#500)
- An agent task that finishes while the server is restarting has its uncommitted work committed, the same as one that finishes under the server that started it. (#500)
- The review diff no longer fails for a whole work item when a file in it is not UTF-8. (#500)
- "the 'vector' extra is not installed" names the command for your install: the version you run, on the Python it runs on, with uv, pipx or pip as you installed it. Homebrew installs are told the formula does not ship the extra, instead of being given a second install. (#500)
- Retrying or resuming an item stopped at a node with `on_base_changed` leaves the rebase to that node, so a moved base restarts its span instead of skipping it. (#500)
- The note an agent gets after a base-change restart names the base branch, not the item's own branch. (#500)

- `kraft item resume` on an item that stopped now says how to go on: `kraft item retry ID` (which takes `--steer` too), or `approve` or `reject` when it waits at a gate. 1.4 said only "not paused". (#503)
- `kraft item resolve` and `kraft item reopen` print one line naming the thread and where it sits, such as `resolved thread a893… on calc.py:+9 to +11`, not the whole thread. `--json` is unchanged. (#503)
- `kraft view list` shows the first line of a title stored with a line break, instead of wrapping the row under its ID column, and leaves out control characters and bidi embeddings, overrides and isolates. (#503)
- A schedule's title (`intake.yaml`; a `policy.yaml` trigger before 2.0) is cleaned up rather than filed as written: with a line break, its first line is the title and the rest goes to the top of the description; a tab becomes a space and other control characters, and bidi embeddings, overrides and isolates, are dropped; a blank title takes the description's first line, and a schedule with neither is skipped with a warning. 1.4 kept a block scalar's trailing line break (`title: >`) in the title. (#503)
- Before an item starts, `kraft view docs ID` (and `--json`) lists the spec and plan it was filed with as `attachment:ID:spec` and `attachment:ID:plan`, and `kraft view doc` prints them. They are read from the copy Kraft stored at filing, which is what the item's worktree gets, not from the file in your checkout as in 1.4. Opening one in an editor (`--open`) waits until the item has started. (#503)
- An HTTP `422` from a request body Kraft refuses no longer starts the message with pydantic's `Value error, `. (#503)
- `kraft item pause` now pauses an item waiting out a rate limit, which 1.4 refused with a 409, and `kraft item retry` on a paused item says to resume it instead of "work item is not stopped". (#503)

- Pausing or cancelling an item while Kraft rebases its branch (`mr_rebase`), or stopping the server then, now stops the rebase: Kraft ends git, puts the branch back where it was, and only then returns the files it set aside. Before, the rebase carried on alone and could rewrite a cancelled item's branch, and the next start found it half done. (#514)

- A failing test on a changed-test-scope verify (such as `quick-task`'s) now stops with the failing command, its scope and exit code in the reason (``task failed in node verify: test_changed_scopes [builtin] — tests failed: `npm test` (scope `**`, exit 1)``), and the same detail in the stop's `facts` with the session holding its output. The same reason text also goes into the `work_item_needs_human` event and the auto-escalation's prompt, so any secret written inline in a `test_command` now appears there too. (#515)
- `kraft view logs ID` on a stopped item now shows the session that stopped it, not the auto-escalation that started right after; stderr names the newer session and how to read it. `-f` still follows the newest session. (#515)

- Fix: the Kraft and Kraft Lite plugin skills and the worker methods say what they do as well as when to use them, and several stated facts that no longer matched Kraft (the status of an item waiting at a gate, where a rejected gate re-enters, which node runs the test suite, what `kraft admin restart` does with no server). (#542)

- Fix: a sandboxed worker with a `network:` policy can read its own work item's events (`kraft view events`), so a work brief or review brief written in a sandbox has the test, review and fix-loop history. (#545)

- Fix: six cases the shipped skills left unsettled, among them what a gate reviewer reports when the final review has no document, and where a failed code review says why. (#556)

- Fix: a sandboxed worker now sees only its own work item's files in Kraft's results folder, instead of every work item's diffs, findings and instructions. (#559)

- Fix: the Analytics screen's captions now match what is computed (By node minutes are a mean per session, Lead time runs to completion, the cost notes say Kraft's estimates are included, fix rounds and the By repo column are named for what they count), and "open MR → checks done" no longer reads 0s on the shipped default chain. (#633)

- Fix: the Analytics screen counts repairs (rounds 1 and up) in the Overview and By repo, with one definition on desktop and phone; the cost-per-completed caption reads plainly; the cost note appears once, under By node. (#640)
