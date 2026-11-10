# Kraft Lite

Run a Kraft chain inside your coding-agent session, with no server to start. [Kraft](https://kraft-sdlc.dev/) is a local orchestrator that takes a coding agent from spec to merge request, stopping only when a decision is yours. Kraft Lite is its attended half: the same chains, walked in the session in front of you. It stops at each gate for your answer and caps its fix loops. It resumes in a new session, but it doesn't run while you are away or wait on CI for you. For that, use Kraft.

## Kraft or Lite

| | Pick it when |
|---|---|
| **Kraft Lite** | You want the chain's order, gates and caps in the session you are already in, with nothing to install but a plugin. |
| **[Kraft](https://kraft-sdlc.dev/)** | Work should run while you are away, wait on CI, stay under a spend cap, show on a web board or be searched across repos. |

Both read the same chain files.

## Install

Lite ships in the Kraft marketplace, beside the `kraft` plugin. It needs Python 3.10 or newer and nothing else.

```bash
claude plugin marketplace add itsOmidKarami/kraft
claude plugin install kraft-lite@kraft
```

Add `--scope project` to either command to keep it to one repo. Update with `claude plugin update kraft-lite`. Cursor, Gemini CLI, Antigravity, Amp and OpenCode load it too: see [other harnesses](https://kraft-sdlc.dev/guides/kraft-lite#other-harnesses).

## Your first run

1. In a session in the repo, run `/kraft-lite:status`. It reports that no chain exists yet.
2. Run `/kraft-lite:start "<work>"`. Lite freezes Kraft's default chain and runs the first node.
3. Run `/kraft-lite:next` to run each node after it. It also resumes after a gate, a new session or a cleared context.
4. At a gate, run `/kraft-lite:gate` to read what needs deciding, then approve or reject.
5. Run `/kraft-lite:status` at any time to see the live node and the fix attempts spent.

`/kraft-lite:init` is optional. Run it once per repo to write a `.kraft-lite/bindings.yaml` that binds a task to your own skill, instruction or test command. A task you leave unbound follows [Kraft's own method](https://kraft-sdlc.dev/reference/kraft-lite#krafts-methods) for it, the spec writer and the planner included.

## Commands

| Command | What it does |
|---|---|
| `/kraft-lite:init` | Writes `.kraft-lite/bindings.yaml`, once per repo. |
| `/kraft-lite:start "<work>"` | Freezes the chain and runs the first node. |
| `/kraft-lite:next` | Runs the next node. Also the resume point. |
| `/kraft-lite:gate` | Presents a gate and records your answer. |
| `/kraft-lite:status` | Reports where each chain has got to. |
| `/kraft-lite:run-slices` | Controller: builds the parts of a plan that stand alone as slices, one session each. |
| `/kraft-lite:build-slice` | Child: builds one slice for its controller. |

## Learn more

- [Kraft Lite guide](https://kraft-sdlc.dev/guides/kraft-lite): install on other harnesses, a first run, bindings and chain sources.
- [Kraft Lite reference](https://kraft-sdlc.dev/reference/kraft-lite): state fields, slices, fix-loop order, what Lite reads past and refuses, and moving an older directory.

## Tests

From `plugins/kraft-lite/`, run the suite with your Python test runner (`pytest tests -q`). From the Kraft repo root, use `just test plugins/kraft-lite/tests`.

## Contributing

This plugin lives in `plugins/kraft-lite/` in the [Kraft](https://github.com/itsOmidKarami/kraft) monorepo, the same repository `kraft` itself ships from. Open a normal <!-- vale Kraft.MergeRequest = NO -->pull request<!-- vale Kraft.MergeRequest = YES --> there. The root [CONTRIBUTING.md](../../CONTRIBUTING.md) covers tests, the release-label convention and how a merge becomes a release.

## License

Apache-2.0. See [LICENSE](LICENSE).
