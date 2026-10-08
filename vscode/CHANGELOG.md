# Changelog

Notable changes to the Kraft VS Code extension, newest first. The extension is
released together with Kraft, at the same version, so each section below is
Kraft's release notes for that version. The release workflow writes them; do not
edit this file by hand. Earlier releases are in Kraft's
[CHANGELOG](https://github.com/itsOmidKarami/kraft/blob/main/CHANGELOG.md).

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
