# Changelog

Notable changes to Kraft, newest first. The release workflow writes each
section from the `## Changelog` part of the pull requests it ships; do not
edit this file by hand. Releases before 1.0.0 are
listed on the [GitHub releases page](https://github.com/itsOmidKarami/kraft/releases).

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

## 1.4.0

### New

- The dollar caps (`budget.work_item_usd`, `budget.daily_usd`, `--budget`) now count Codex sessions at an estimate from the model they were launched with, now that Kraft's price table includes OpenAI models. They also estimate Amp sessions on a priced model. Spend Kraft still cannot price, such as Cursor or a model missing from the table, is counted as $0 as before, but the item's timeline and `kraft admin doctor` now warn about it. Use `token_budget` to bound that spend. (#314)

- Sandbox credentials take `phase` (only the setup command or only agent sessions get them) and `source` (read the value from a named daemon variable, which never reaches the container). (#325)

- Sandbox `kind: kit`: run a Docker Sandbox Kit you built, pinned by digest, with exactly the egress, credentials and limits it declares; `sandbox.yaml` binds each Kit credential to a daemon variable. (#332)

- `kraft item set-policy ID --policy budget_usd=none` clears an item's dollar cap, which is the way past a stop on spend a harness never reported. It is refused under a `maxima.work_item.budget_usd`.
- A session that sends more than a model's long-context size, for example OpenAI's 272k tokens, is now estimated at that higher rate. This applies when its log shows each request (Claude, Amp). Codex is still estimated at the base rate. (#336)

### Fixes

- `kraft view list --status` rejects an unknown status instead of showing an empty board.
- `kraft item create --skip-nodes` refuses a node marked `skippable: false`.
- `kraft admin templates lint` reports an agent task its harness can't launch (for example `profile: fast` on Codex), instead of leaving it to fail at launch.
- `kraft admin doctor`: no capabilities upgrade guide on a fresh home, adopted capabilities are no longer listed, and a repo without Kraft's MCP registration is a warning naming the harness that would be refused.
- `kraft admin harnesses` shows which chains select each harness; `kraft admin start --host` says how other commands reach the instance. (#323)

- The VS Code extension's Marketplace page now explains what Kraft is, how to get started, and what each feature does, with screenshots and links to the docs. (#328)

- Raise-budget's refusal for a stopped item's policy cap now points at `set-policy` instead of a `policy.yaml` edit that can't reach an already-filed item; the board's Done group no longer claims a fixed 30-day auto-archive window; a few other refusal messages no longer leak an internal ruling citation. (#330)

- VS Code: **Review Changes** in a gate document's title bar now opens the diff; before, it did nothing.
- VS Code: each review finding is listed once in the Problems panel instead of twice.
- VS Code: the activity-bar icon shows a K instead of a solid grey square. (#331)

- The review diff (web UI, VS Code, `kraft view diff`) no longer lists Kraft's own untracked session notes as changed files.
- VS Code: review findings are marked on the diff's lines, with the message beside the line, so minor findings show on added lines too. (#340)

## 1.3.1

### Fixes

- Repo lockfile update (urllib3 2.8.0). No change to the installed package. (#318)

## 1.3.0

### New

- Kraft now runs on Python 3.12 and 3.13 as well as 3.14. (#316)

### Fixes

- The seeded `policy.yaml` and `quick-task.yaml` comments now describe each setting in user terms, with no internal notes. Existing installs keep their own copies.
- Troubleshooting now covers the `budget_usd cannot be checked` stop, and a `budget_usd` cap set on one node, step or task. (#313)

## 1.2.2

### Fixes

- The VS Code extension no longer goes read-only when it connects to a Kraft daemon running from a source checkout (a `.devN` or `+local` version). (#308)

- `/api/health`, the sidebar and the empty board now show the address the server actually listens on. Before, a server started with `--port` or `KRAFT_PORT` still showed `access.yaml`'s port.
- `kraft admin start --port N` now says when other `kraft` commands will still dial a different port, and how to reach this instance (`KRAFT_PORT=N`, or `port: N` in `access.yaml`).
- `kraft admin doctor` on a home where Kraft has never run now says so first. Its `harnesses.yaml` row says to start `kraft` once instead of showing a raw file-not-found error. (#310)

## 1.2.1

### Fixes

- Fix: a release publishes to PyPI, the VS Code Marketplace and Homebrew again. v1.2.0 reached only the GitHub release, because PyPI rejected the extension package the release put next to the wheel. (#305)

## 1.2.0

### New

- Four new kraft plugin skills (triage, review, steer, doctor); skill descriptions and guidance corrected: stale CLI verbs, gate/start safety checks. (#221)

- Add: `POST /api/templates/check` validates an unsaved config file as its save would, and template lint issues (API and `kraft admin templates lint`) report line and column. (#232)

- Add: a VS Code extension for Kraft: a sidebar board, gates, diff review with findings and line comments, and config-file diagnostics. (#233)

- New: cut alpha, beta and rc pre-releases from the release workflow, and install them with `kraft admin update --channel`. (#234)

- Add `kraft item reply` and a review-flow API — line-anchored comment threads on a pending gate, review submission with a must-fix gate on approval, a compare endpoint for any two attempts, and an agent that answers threads after a plain "comment" review. (#236)

- Comment and request-changes threads now work at any point in a run, not just at a pending gate; new `kraft view threads`/`compare`, `kraft item comment`/`resolve`/`reopen`, and `kraft item review` verbs (and matching MCP tools) for reviewing a change headlessly. (#240)

- **Behaviour change: the docker sandbox now keeps workers off your other branches and never runs a tool policy unenforced.** A sandboxed worker's ref changes stay in a store of its own, and only its item branch reaches your repository. A sandboxed Cursor or Codex task with `allowed_tools`, `deny_tools` or a grant beyond `git-commit` now stops at launch, because their permission hook cannot run in a container; use Amp or OpenCode for a sandboxed policy, or remove the policy. Sandboxed workers now get their own `HOME`, credentials named in `env_passthrough`, and your git identity, so every shipped harness that can authenticate by env runs in a sandbox. `kraft admin doctor` checks the docker daemon and image. (#245)

- **Sandboxed tasks run on Podman and on rootless Docker and Podman, and on SELinux hosts when you say how.** A new optional `sandbox.yaml` picks the container CLI (`cli: podman`) and what to do where SELinux enforces (`selinux: relabel` or `disable`). Without an answer there, a sandboxed task now stops with a message naming both, instead of failing on denied mounts. On rootless runtimes, what a worker writes is now yours rather than owned by a subordinate uid. A `docker` that is Podman underneath is recognised. `kraft admin doctor` names the runtime it found. (#247)

- **Sandboxed tasks work behind a corporate proxy and CA.** The daemon's proxy settings are forwarded into the container; a proxy on the host's loopback cannot work from a container, and `kraft admin doctor` now says so. An extra root CA, from the new `sandbox.yaml` `ca_bundle` or the daemon's `SSL_CERT_FILE`, is combined with the image's own roots and trusted by every common CLI in the container. Host workers now also keep `REQUESTS_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS`, `GIT_SSL_CAINFO`, `CURL_CA_BUNDLE`, `SSL_CERT_DIR` and `ALL_PROXY`. (#248)

- **Sandboxed tasks can have resource limits.** `sandbox.resources: {cpu, memory, pids}` becomes `--cpus`, `--memory` (swap included where the runtime can limit it) and `--pids-limit`, and every sandboxed run gets a default of 4096 processes. A limit the runtime cannot enforce stops the task with a message naming the fix instead of being silently dropped. A task killed by its memory limit is recorded (`sandbox_oom_killed`) and stops for a person naming the limit, rather than counting as the agent failing. `kraft admin doctor` shows which limits the runtime enforces. (#249)

- Feature: sandboxed workers get deny-by-default egress (`sandbox.network`, including Docker Desktop and podman machine), callbacks to Kraft from inside the container, and proxy-injected credentials (`sandbox.credentials`) so API keys never enter the container. (#256)

- Add `kraft item raise-budget` and the MCP `raise_budget` tool, which raise a stopped item's own dollar cap and retry it, like the board's Raise budget button.
- Add `ci_checks: false` to `repos.yaml`: on a repo with no CI, the CI waits before and after the merge pass at once instead of waiting on checks that never come. (#279)

- Security: failed logins are throttled per address (429 after 5 in 15 minutes), the session cookie is `Secure` over HTTPS, and a new `run/trigger-token` authenticates `POST /api/triggers` only, for CI and webhooks. (#284)

- Change: workspace members are checked out as worktrees of their connected repositories, and a workspace whose root or members set a sandbox now runs sandboxed (it was refused before). (#289)

- **Changed default:** `POST /api/work-items` now files the item paused unless the body sets `autostart: true`. Before, a raw API caller started the item, and spent tokens, by default. The board, `kraft item create` and the MCP tool are unaffected.
- **Changed default:** a repo's `default_chain_template` in `repos.yaml` now applies to every way of filing work: `kraft item create`, the MCP `create_work_item` tool, the board's New work item dialog, `POST /api/work-items` and `POST /api/triggers`. Before, it applied only to auto-intake. Naming a chain still wins.
- Fixed: `kraft repo connect .`, and any other relative path given to `repo connect`/`disconnect`, `item create --repo`, `admin reindex --repo` or `view list`/`watch --repo`, is read from where you stand instead of from the Kraft server's directory. `POST /api/repos` refuses a relative path with a 422. (#290)

### Fixes

- Fix: a database connection could be left open when the writer failed or the search index was corrupt. (#220)

- Fix: the plugin READMEs and skills are corrected and link to the current docs. (#222)

- `findings_measured` events now include a `dropped` count when a reviewer's malformed findings were discarded. Internal skills trimmed of contracts Kraft already injects. (#223)

- Fix: after a gate rejection sends work back to implementation, the item page no longer shows the plan's task list and progress bar for a run that isn't following the plan. (#237)

- Fix: review submission, attempt comparison and the reply agent handle refusals, renamed files and agent failures correctly. (#238)

- Fix: a running agent session's spend now shows as a live estimate (`~$X (est.)`) instead of a stale `$0.666+`, priced correctly against Claude Code's own 1-hour cache-write rate, and a dollar budget cap can stop a long session before it exits -- or after a crash that reported no final cost -- instead of losing that spend to a blank total, without double-counting a paused-then-resumed session's spend. (#239)

- Fix: `base_ref` no longer goes stale on retry/resume after a branch is manually rebased onto a new upstream head outside Kraft. (#242)

- Docs: the Kraft plugin's gates and review skills now leave line-level review threads and submit reviews, and the agent guide lists the review MCP tools. (#243)

- Fix: the chain-review step now raises the implementation time cap when the plan has more tasks than the cap can hold, instead of letting the run stop mid-plan. (#244)

- Fix: Kraft works with only the Claude Code plugin installed, and `kraft admin init` is no longer needed. Workers use the permission tool the plugin registers, and a Claude launch with no Kraft MCP server registered is refused with the fix named instead of stopping with 0 tokens. (#260)

- Fix: Kraft reads a repository's committed Claude Code plugin settings the way Claude Code does, so a repository that disables the Kraft plugin is no longer treated as having it. (#264)

- Fix: a session killed by its sandbox memory limit is no longer sometimes recorded as an ordinary failure. (#265)

- Fix: an escalation turn's `git-push` grant now covers only a push to its work item's own branch, and allows `--force-with-lease` but not a plain `--force`. (#268)

- Fix: the API also refuses a worker session's approve, reject, pause, resume, skip, abandon or retry on its own work item, and the security docs say plainly what that does and does not stop. (#269)

- Fix: a sandboxed worker can no longer move your branches or stash by planting git state in its worktree; Kraft stops for you instead. (#272)

- Fix: `kraft admin start --detach` now waits for the server to actually answer `/api/health`, not just for its pidfile to appear. (#273)

- Fix: opening `/docs` or `/redoc` in a browser shows FastAPI's API docs instead of the board.
- Fix: the Analytics "Throughput by week" chart says it counts merged items, and reads "nothing merged in this range" instead of contradicting the Completed count.
- Fix: `$KRAFT_HOME/run` is now `0700`, tightened on every start, and new databases and session logs in it are `0600`. (#275)

- Fix: a session a sandbox memory limit killed is no longer sometimes recorded as an ordinary failure when Docker drops its out-of-memory flag; it stops as an unconfirmed memory-limit kill instead. (#277)

- Fix: the board and docs site no longer load fonts from Google; the API refuses a worker's complete, cancel, escalate and attachment, override, policy and budget changes on its own item; and an escalation turn can no longer approve or reject its own item's gates. (#280)

- Security: bump PyJWT to 2.15.1 (Dependabot #18). (#281)

- Fix: an escalation turn can no longer raise or change its own work item's budget or policy; like a gate, that's a person's decision. (#282)

- Fix: Raise budget appears only when the item's own dollar cap stopped it. A stop on a policy `budget_usd`, a `token_budget` or the daily cap names that cap and how to raise it, and `kraft item raise-budget` refuses it. (#283)

- Fix: Claude workers are no longer refused when Kraft's MCP server is registered at local scope or in a managed `managed-mcp.json`; a Kraft plugin enabled but not installed now counts as not registered, and `kraft admin doctor` checks each connected repo. (#285)

- Fixed: every server route that takes a repo path refuses a relative one instead of reading it against the server's directory: `POST /api/work-items` and `POST /api/triggers` answer 422, `PATCH`/`DELETE /api/repos?path=` answer 404. Fixed: analytics filtered to the `default` chain now counts items filed without naming a chain. (#291)

- Fix: Kraft no longer force-pushes over commits a person pushed to an item's merge request branch; it stops and says how to bring them in. Fix: connecting a workspace root reuses a member you already connected on its own instead of adding an empty entry for its submodule, and `kraft admin doctor` warns about existing duplicates. (#292)

- The `list_work_items` MCP tool now names the statuses you can filter on, including `needs_human`, and no longer lists `failed`, which never matched anything.
- The docs site's canonical links now point at the real pages under `/kraft/`. The dead "Add MCP Server" and "Copy MCP Server URL" menu items are gone, `robots.txt` exists, and the search dialog has a proper accessible name.
- The PyPI page lists classifiers: Beta, console, developers, macOS and Linux, Python 3.14.
- The docs say that `--auto-gate` is on by default, explain how to install the VS Code extension, and cover an agent that isn't logged in and a port that is already in use. (#304)

## 1.1.0

### New

- New harness: Cursor's agent CLI (`agent -p`), checked with real runs on
  cursor-agent 2026.09.18-9a7762b (Kraft-bosip). It runs in `--auto-review`,
  Cursor's classifier mode; `permission_mode: force` overrides it. Every
  launch points `CURSOR_CONFIG_DIR` at a Kraft-owned directory under
  `$KRAFT_HOME/run`, rewritten with Cursor's default config and commit
  attribution off, so a worker's commits carry no `Co-authored-by: Cursor`
  trailer and the classifier no longer refuses them (Kraft-umakq). Your own
  `~/.cursor` is untouched. Tokens are read off the log; Cursor reports no cost. (#192)

- **Behaviour change: the never-signal rule is no longer on every agent launch.** It is now an opt-in steering profile, `never-signal-processes-you-didnt-start`, that a repository names in `repos.yaml` (`steering: [never-signal-processes-you-didnt-start]`). Its text is unchanged. **Existing installs lose it on upgrade until you act.** Templates are never reseeded: an install first set up on 1.0.x has no such profile, and one upgraded from 0.x has it but no repo names it. If any of your repositories run tests that start their own servers, run `kraft admin doctor` after updating: it prints the exact YAML for the library profile (skip that part if your library.yaml already has one) and for the `repos.yaml` line. An item already in flight froze its steering at intake, so it runs without the rule until it is retried or re-filed. (#193)

- Codex workers now work on a default install. Codex's sandbox refused
  writes outside the worktree, so a worker could not write its result file
  under `~/.kraft/run/results` or commit (a linked worktree commits into the
  main checkout's `.git`), and every codex item failed. Kraft now grants
  both directories on every launch through a new `writable_dirs` capability
  (Kraft-rs9pk).
- Codex runs in approve-for-me mode by default: the workspace-write sandbox
  plus Codex's automatic reviewer for anything outside it, the counterpart of
  Claude's `auto`. `permission_mode` still overrides it per harness profile or
  task.
- Fix: resuming a codex session failed at argument parsing, because
  `codex exec resume` rejects `-s` after `resume`. Every codex option is now a
  `-c` config key, which parses on both paths.
- Fix: a codex usage limit crashed the run instead of parking the item as
  rate limited. (#194)

- New harness: OpenCode (`opencode run`), for any provider OpenCode knows,
  including a ChatGPT login and OpenCode's free models (Kraft-nv1f1). Checked
  against opencode 2.0.15 with real Kraft work items on `opencode/big-pickle`
  and `openai/gpt-5.6-luna`. Every launch passes `--auto`, since `run`
  otherwise rejects every permission request. There is no `effort`: name a
  variant in the model id (`provider/model#high`). Usage comes from
  `opencode session export`, because opencode 2.x's JSON log leaves out the
  last step's tokens (Kraft-ihoen). (#195)

- New harness: Amp (`amp -x`), with Amp's mode (`low` to `ultra`) as
  `effort` (Kraft-gvrke). Checked with a real Kraft work item on a logged-in
  amp. Resuming works: every launch passes `--no-archive-after-execute`,
  since Amp archives a thread after `-x` and refuses to continue an
  archived one. Amp reports no cost Kraft can read, so none is recorded. (#196)

- Kraft's permission gate now answers Codex workers too. When a task's
  policy has something to enforce, the launch passes Codex a `PreToolUse`
  hook with `-c` and trusts only that hook, by the hash `codex app-server`
  reports; `--dangerously-bypass-hook-trust` is never used, and a launch
  whose hook Codex won't trust is refused. `deny_tools` and `allowed_tools`
  now work on Codex (`apply_patch` is checked as `Write` and `Edit`), each
  decision is logged on the timeline, and a resumed thread keeps the hook.
  Codex's web search never reaches the hook, so policy that must deny
  `WebSearch` refuses the launch (Kraft-4in7z.3).
- OpenCode and Amp workers now honour a task's `deny_tools` and
  `allowed_tools`: Kraft writes them into that launch's own config (OpenCode's
  `OPENCODE_CONFIG_CONTENT` with `--standalone`, a per-session Amp
  `--settings-file`), and the CLI enforces them. Denying `Bash` also denies
  OpenCode's code mode. An allowlisted tool on Amp is allowed outright, past
  Amp's own built-in asks. A denied tool name the CLI has no tool for refuses
  the launch; an allowlisted one grants nothing. These decisions aren't logged on the timeline, and grants aren't
  applied (Kraft-4in7z.4, Kraft-4in7z.2).
- A Codex, Cursor or other non-Claude worker that outlived a Kraft restart
  now has its tokens read with its own harness's log reader instead of
  Claude's, which found none; each session now records its harness, and
  older sessions keep Claude's reader (Kraft-9elw1).
- Kraft's permission gate now answers Cursor workers too: a `preToolUse`
  hook, written into the worktree's `.cursor/hooks.json` (never committed)
  when the task's policy has something to enforce, applies `deny_tools` and
  `allowed_tools` (which Cursor launches can now use) and logs each decision
  on the item's timeline, with the harness and Cursor's own tool name.
  Everything policy doesn't decide is left to Cursor's classifier, and a hook
  allow doesn't override it (Kraft-4in7z, Kraft-4in7z.6). New `grants:` in
  policy (`git-commit`, `git-rebase`, `git-push`) let a task's gate allow
  exactly one plain git invocation of that operation, even outside
  `allowed_tools`; a commit message with `$` or `!`, or anything chained,
  goes to the classifier when there is no allowlist, and is denied under
  one. A granted push must name a plain remote, and `--delete`, `--mirror`,
  `--all`, `--prune`, `--repo` and git's `-C` are never granted. A grant is
  the gate's logged allow: Cursor's classifier can still refuse the call
  (Kraft-4in7z.6), and a Claude task under an allowlist without `Bash` has no
  shell tool to make it with (Kraft-4in7z.12). Grants accumulate down the
  layers, and a work item's own override, or a retry's, can drop a grant but
  never add one. An
  escalation turn is granted all three by default, so it can rebase and
  push; `defaults.escalation_grants` in `policy.yaml` narrows that.
- New `policy.yaml` key `escalation_harness` picks the harness an escalation
  turn runs on (Kraft-wge0e). Unset, it is `claude`, as before. Set it in
  `defaults:`, on a repository, chain or node, or for one item with
  `kraft item set-policy`; `item` follows the harness the item's own work
  last ran on. An unknown profile is refused when `policy.yaml` is read. A
  resumed escalation or paused task now finds its session id with its own
  harness's reader, and a resumed codex thread records only what it added. (#216)

### Fixes

- Before a workspace item's draft merge requests open, each changed member is
  now rebased onto its own default branch, and the root's pointer is updated to
  match. Member drafts no longer open on the stale base they were cut from.
- The pre-MR rebase's `git rebase --abort` now has its own 30-second limit. A
  git hook that hangs during the abort stops the item for a human, with a
  message saying the worktree was left mid-rebase, instead of holding the
  worker slot forever. (#208)

- `kraft admin templates lint --dir PATH` lints a template directory
  in-process, with no server and no `$KRAFT_HOME`. It doesn't check an
  installed skills override or `policy.yaml` ceilings. (#210)

- A workspace item whose root rebase conflicts only because the root's base
  branch also moved a member's pointer now stops with a sentence naming the
  member and saying how to resolve it, after git's own conflict text. (#211)

## 1.0.8

- Fix: a `chain_revision_approval` gate now gets the same board, action-bar
  and search Approve treatment `human_review_approval` has -- its own prompt
  text, and Approve routes to the item page instead of firing a blind
  request that always 409s (the gate's Approve needs a digest only the
  artifact pane carries).

- Fix: a `read_only_violated` event (a read_only step or node changed the
  worktree) now shows what changed on the Timeline instead of the bare event
  name with no detail.

## 1.0.7

- Fix: an in-flight work item's own repository steering no longer silently
  drops if `repos.yaml`'s `path:` for that repository is hand-edited while it
  runs. The launch now finds it by the repo the item was filed against, not
  only by the repository's current path. (A workspace item's fanned-out
  member repositories are still looked up by their live path only, so a
  member's path edited mid-flight can still lose its steering -- Kraft-ku1um.)
- Fix: `kraft admin doctor` now fails a row for a connected repo whose
  `steering:` in repos.yaml names a profile the template library does not
  define, naming the repo and the missing profile. Previously a hand-edited
  repos.yaml (or a library edited outside Kraft) read healthy until the next
  work item's intake refused it.

## 1.0.6

- Fix: a gate's agent reviewer no longer approves a chain revision that
  changed after it read it — its approval is now bound to the digest of
  what it actually read, the same way a person's approval already is.
- Fix: a gate whose `auto_review` task selects an agent profile carrying its
  own `fallback:` list is now refused at launch, naming the profile, instead
  of silently ignoring the list and leaving a rate-limited review undecided.

## 1.0.5

- **Fix: a `policy.yaml` cron trigger naming an unconnected repo is now
  skipped, not filed.** `POST /work-items` and `POST /triggers` already
  refused a repo Kraft has not connected (`kraft repo connect`, Kraft-ta8nv);
  `triggers.tick` called intake directly and missed that door. It now skips
  the trigger with a logged warning and still fires the other due triggers in
  the same tick.

## 1.0.4

- Fix: `kraft item set-overrides` (and MCP `set_agent_overrides`) now refuses
  a `model`, `escalate_model` or `effort` that any agent task's harness in the
  item's chain would refuse, at the door instead of hours later when that
  task launches -- the same check a per-node override already got.

- Fix: `kraft admin doctor` now tells an install first set up on 1.0.1 or
  1.0.2 to add the pre-MR rebase from 1.0.3. The capability was recorded
  as 1.0.1, so doctor only flagged installs seeded before that.

## 1.0.3

- **Fix: the default chain's draft merge request now rebases before it
  opens.** `draft_merge_request` opened against the item's base branch as it
  stood when the worktree was cut, not wherever the base moved to while the
  item ran -- Template Schema V1 had dropped the pre-MR rebase every earlier
  chain had. `draft_merge_request` now runs a `kraft.mr_rebase` builtin task
  first, then opens the draft.
  - **This does not reach any existing install on its own.**
    `$KRAFT_HOME/templates/` is seeded once, at first run, and never
    overwritten -- `kraft admin update` replaces Kraft itself, not your
    templates. So **every install running before this release**, not only a
    hand-customized chain, keeps opening draft MRs on a stale base until you
    add the task yourself: run `kraft admin doctor` after updating, which
    lists this (and every other capability your templates are missing) with
    the exact YAML to paste in. An item already running when you update keeps
    the chain it was filed against; only an item filed after you update the
    template gets the fix.

## 1.0.2

- Fix: a session log too big to read whole now shows where it was cut off,
  with a link to the full plain-text log, and offers a download instead of
  copying the whole log into the page (Kraft-qmjk1).
- Codex: Kraft reads codex's `--json` log for token usage, the session id
  and usage limits (Kraft-w3kot).

## 1.0.1

- Fix: the Appearance settings page's palette, mode, density and board
  controls now show as disabled while your theme is still loading, instead of
  silently doing nothing if you clicked one in that window.

## 1.0.0

Kraft 1.0 makes the chain a piece of data you can read, lint, and reason
about. Chains are typed YAML under Template Schema V1. Policy is layered from
the instance down to a single task and enforced on every agent launch, and
every scope can be bounded in time and spend. The plan can revise the rest of
the chain, and a person approves the revision before it applies. Agent tasks pick
a named model tier, and can fall back to another harness or model when one is
rate-limited or unavailable. A person, not an agent, decides whether to skip,
abandon, or give more room.

### Before you upgrade from 0.x

- **Finish or abandon every in-flight work item first.** An item filed under
  0.x has no V1 chain, and 1.0 refuses to run it.
- **`kraft admin update` replaces a pre-V1 template home**, after asking (or
  with `-y`). The old home is moved to `~/.kraft/templates.pre-v1-<timestamp>`.
  There is no migration of chains: re-apply your tuning (model and effort
  pins, custom nodes) by hand from that backup.
  - Carried across unchanged: `access.yaml`, `notify.yaml`, `theme.yaml`,
    `repos.yaml`, `intake.yaml`, `steering/`, `harnesses/`.
  - `policy.yaml` keeps your value for every key V1 still has, and prints every
    key it drops.
- **Steering files become library steering profiles.** On its first start,
  1.0 adds each `templates/steering/<name>.md` to `library.yaml` as
  `steering: {<name>: {instructions: <the file's text>}}` and moves the
  directory to `templates/steering.pre-1.0/`. `repos.yaml`'s `steering:` names
  keep working. A name `library.yaml` already defines keeps the library's
  text; that file (and any empty one) is only in the moved-aside copy, and the
  log says so. An item already in flight keeps its steering: it reads its
  repository's names against the library at each launch, as it read the
  files before.
- **Harnesses are renamed:** `codex_default` → `codex`, `claude_review`
  → `claude`. A chain or policy that names an old harness must be updated.
- **Chain API routes moved** from `/api/templates/{id}` to
  `/api/templates/chains/{id}` (and `/resolved`). The old paths answer 404.
- **Check for a second `kraft` on your PATH**, such as an old Homebrew
  install. MCP servers run `kraft` by name; `kraft admin doctor` now fails when
  another install shadows this one.
- **Restart any agent session that uses Kraft's MCP tools** after upgrading.

### Highlights

- **Chains are data.** Nodes, steps, tasks and gates are typed and ordered.
  Recovery handlers, fix loops with a judge, and stuck escalation are part of
  the chain rather than hidden in code. A reusable **Library** of tasks, steps,
  nodes and steering lets chains extend shared parts. The Library has a
  Settings page, an API and a CLI.
- **Policy reaches every launch.** Policy is layered instance → repository →
  work item → chain → node → step → task. It applies to dispatch, gate
  review, escalation, the judge and recovery alike. A work item can carry its
  own policy override and its own base branch.
- **Caps at every level.** `time_cap_minutes` (running time),
  `total_time_cap_minutes` (wall clock), gate timeouts, `token_budget` and
  `budget_usd` can be set on any scope. `policy.yaml` sets defaults and maxima
  per level: `work_item`, `nodes`, `steps`, `tasks`. A child scope can't exceed
  its parent. Hitting a cap stops the item for a person and is never recorded
  as a code failure.
- **The plan can revise the chain.** After planning, a `chain_revision` node
  proposes the rest of the chain, and a gate shows you the change before it
  applies. A revision can never drop the merge-request nodes. A revision
  computed from a chain that has since changed is refused. The final gate is
  now called `final_review`.
- **Stops tell you what to do next.** A stopped item carries a
  `suggested_action` (skip, retry or abandon, with a reason). The API, the CLI
  and escalation all show it. Only a person may skip, abandon, or reset a
  limit. An escalation agent may retry its own work, and says so when it
  isn't allowed to do more.
- **Harnesses you can see.** A Harnesses page, `/api/harnesses/profiles` and
  `/providers`, and `kraft admin harnesses` show each harness, its provider,
  and which tasks use it. Each provider lists its capabilities: the CLI flag
  each one becomes, what it accepts, and what every launch forces.
- **Model tiers and fallback.** Agent profiles (`deep`, `strong`, `fast`) name
  a model tier once, with a model per provider, and tasks select a tier
  instead of spelling out a model. An opt-in fallback list moves a rate-limited
  or unavailable launch to another harness, tier or model at once, and every
  switch is logged where you will see it.
- **One steering store.** Steering profiles live in the Library. A task or a
  repository names them, and their text is frozen into the item at intake.

### Added

- **Agent profiles:** `harnesses.yaml` gains `profiles:`, named model tiers
  (`deep`, `strong`, `fast`) with a model per provider. A library task picks
  one with `profile:` instead of its own `model:`/`effort:`. The profile is
  read live at each launch. A task whose profile names no model for its
  harness's provider fails `kraft admin doctor` and stops for a human, and no
  other model is substituted. The shipped `implementer`, `repair_*` and
  `strict_judge` tasks use `profile: strong`, which is the same `sonnet` at
  `high` effort as before. An existing install keeps its files and runs
  unchanged. `kraft admin doctor` says how to adopt profiles.
- `kraft item create` matches intake: `--skip-nodes`, `--budget`,
  `--node-override`, `--autostart`. An agent asking for autostart is refused.
- Attachments can be replaced until the item starts
  (`kraft item set-attachments`). A gate trimmed for a missing attachment
  comes back once one is attached. Intake warns about a duplicate open item.
- The default chain writes the merge request's title and description before
  it opens the MR.
- **Intent-driven development:** set `intent_dir:` on a repository, and every
  agent launch is told about its intent tree. The spec, plan, code-review and
  work-brief skills apply it.
- `kraft view show` prints a usage line with cached and uncached tokens apart.
- `kraft admin doctor` checks for a shadowing `kraft` on PATH.
- The Library shows a steering profile's text as text.
- One node of a not-yet-started item can get its own `model`, `effort` and an
  `extra_prompt` appended to each of its agent tasks (`kraft item
  set-node-override`, `--node-override`, MCP `set_node_overrides`). The node's
  values beat the item-wide `set-overrides`; one the node's harness refuses is
  refused when you set it.
- `read_only: true` on a step or an exec node: Kraft checks the worktree
  before and after, and stops the item naming any file it changed. It is
  opt-in, and no shipped chain sets it.
- **Launch fallback:** a `fallback:` list, on an agent profile or on a task,
  says where a launch goes when it is rate-limited or its harness is
  unavailable, in the same dispatch: another harness, another profile, or
  another model or effort. Kraft remembers a rate-limited harness and model
  until its reset and skips it on every item, and logs each switch as a
  `launch_fallback` event, a timeline sentence and a board marker. It is opt-in, and no shipped
  task or profile declares one.

### Changed

- **Token accounting.** Spend reads each result's cumulative `modelUsage`, so
  sub-agent tokens and turns killed before their result are counted. A
  resumed CLI session is counted once. Cache writes and cache reads are stored
  apart from uncached input: `tokens_in` now means uncached input. Totals and
  budgets still count all of them. Sessions recorded before 1.0 keep their
  old total and show "cache not split".
- `kraft admin reload` rereads `policy.yaml` too. A file that doesn't validate
  is refused and the running policy is kept.
- Every settings file loads and saves through a typed model: repositories
  (with a typed sandbox), harnesses, notify, access, intake, theme.
  A repository entry without `enabled` counts as enabled.
- Tool lists in policy hold exact tool names.
- Kraft closes a work item's beads only when its branch actually changed
  something, or a member MR merged. Otherwise a `beads_left_open` event says why.
- Every shipped agent task runs on the `claude` harness.
- The never-signal-processes-you-didn't-start rule is built into every agent
  launch instead of seeded as a steering file.
- **One steering store.** A repository's `steering:` in `repos.yaml` names
  steering profiles from `library.yaml`, like a task's. The text is frozen into
  the work item at intake, so editing a profile reaches items filed afterwards.
  A repository save, an intake, and a library save are each refused when a
  repository would name a profile the library doesn't define. Steering
  profiles are created and edited on Settings → Library; the repository
  steering picker lists them.

### Fixed

- **Sandboxing:** the sandbox wraps the whole work item, and `setup_command`
  never runs on the host. Host git never runs code a sandboxed worker could
  plant: it doesn't recurse into nested repositories, and hooks are off. A
  planted repository stops the item for a person.
- **CI and merge requests:**
  - A cancelled CI run is never a verdict, and one with no successor stops
    promptly.
  - No MR is opened with zero commits.
  - A workspace root MR waits for its own approval and merge.
  - Workspace members:
    - each member rebases onto its own origin;
    - the root pointer moves only to what merged;
    - a rebased head waits for its own CI.
- A worker turn that ends with a background job still running fails and names
  the job, instead of hanging. The implementer task ships with a 120-minute cap.
- Session logs: the live tail reads each byte once, off the event loop. Huge
  logs return their last 2 MB behind a truncation marker.
- `kraft admin doctor` no longer fails on the optional embeddings extra.
  `kraft view show --json` returns the full item.
- The connect probe proposes `just test` only for a real `test` recipe.
- `install-service` works on Linux runners and reinstalls cleanly.
- A repo edit that turns on a repo with no test command, or clears the last test
  command of an enabled one, is refused, also when `enabled` is unset in
  `repos.yaml` (unset means enabled).
- `kraft view logs -n 0 -f` prints only lines written after it started, not the
  log's tail.
- A trigger added to a policy that had none at startup (Settings, or
  `kraft admin reload`) fires without a restart.
- `kraft admin doctor` fails when the embeddings extra is installed but its
  model will not load or encode; `/health` reports the last such failure.
- Filing a work item (`POST /work-items`, `POST /triggers`, `kraft item create`,
  MCP `create_work_item`) against a repo that isn't connected is refused with a
  422 that names `kraft repo connect`, instead of accepting any directory.
- Chain revision:
  - approving one applies the revision you read. The approval sends back the
    digest `kraft view artifact` prints (`kraft item approve --digest`), and a
    revision that changed since is refused with 409;
  - a revision can't skip a node whose only merge request work is in its
    recovery steps.

### Removed

- The legacy template system and the legacy intake path.
- `wait_timeout_minutes`. It is still read and migrated to the wait task's
  `total_time_cap_minutes`, with a warning, but it is refused on write.
- The seeded `steering/README.md` and `steering/never-signal-…` files. An
  existing install's copies become library steering profiles, like any other
  steering file.
- `templates/steering/*.md` as a steering store, the Settings → Steering page
  (its address opens the Library), and the `/api/steering` routes.

### Known limits

- Sibling tasks that launch together can each pass a shared budget check and
  overshoot it by their combined cost (Kraft-ib2sn).
- A sandboxed repository with submodule members is refused (Kraft-ju36l).
- The merge step does not pin the exact head its CI verified (Kraft-vomwx).
- An auto-review agent approving a chain revision gate is checked against the
  gate's last recorded view, not a view of its own (Kraft-rndd1).
- Only Claude launches can trigger a fallback: codex and gemini don't report a
  rate limit yet. They can still be fallback targets.
