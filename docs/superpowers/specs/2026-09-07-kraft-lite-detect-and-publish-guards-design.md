# Kraft Lite — detect's contract, and the guards around publishing

**Status:** design, awaiting approval
**Date:** 2026-09-07
**Chain:** `kl-1efa7d77`

## 1. What this is

Five open beads against `plugins/kraft-lite`, taken as one change because four of
them move the same two seams: what `kl.py detect` returns to the `init` skill, and
what stands between a plugin edit and a published release.

| Bead | P | The defect |
|---|---|---|
| `Kraft-1x2` | P2 | `detect` cannot produce a candidate row for a hook a custom chain invents |
| `Kraft-rje` | P2 | `init` binds `on.ci.poll` to `gh` without checking the forge |
| `Kraft-l5z` | P3 | Registry validation proves a hook key exists, not that it is bound |
| `Kraft-mcx` | P2 | Nothing notices a version bump that was never published |
| `Kraft-3oj` | P3 | `plugin.json` and `marketplace.json` duplicate fields with nothing pinning them |

Two further beads entered the chain and left it at the spec node. `Kraft-asb`
(docs paths that the public split removes) was closed: the guard it wanted already
exists as `test_skills.py:138`, and `justfile:110` runs the suite inside
`lite-publish`, so a bad citation cannot be published. `Kraft-b7p` (MIT license)
was closed on its substantive half — `plugins/kraft-lite/LICENSE` exists and
`justfile:111` enforces it — with the residual recorded on `Kraft-5fx.7`: the
copyright line cannot be diffed against a repo-root `LICENSE` that does not exist
yet.

## 2. Goals

1. **A custom chain can be initialised.** Today `init` serves the packaged chain
   and nothing else; any other chain needs `registry.yaml` written by hand.
2. **`init` must not propose a command that cannot run here.** A binding the repo
   cannot execute is worse than no binding, because it looks finished.
3. **Fail at `start`, not mid-walk.** A registry defect found at node seven has
   already spent six nodes of the human's attention.
4. **The publish path keeps its guarantees when nobody is watching.** Every check
   added here runs from the machine that already holds the credentials.

## 3. Non-goals

- CI credentials for the `lite` remote. GitLab CI has no access to the GitHub
  remote and will not be given a token for it (§7).
- A YAML parser in `kl.py`. The registry stays regex-inspected (§6).
- `claude plugin tag`. It cannot address the commit this project tags (§8).
- Any change to the chain schema, the state backends, or the walk itself.

## 4. `detect --chain` — candidates for the hooks a chain actually names

### 4.1 The defect

`HOOK_KEYWORDS` (`kl.py:616`) is a fixed dict of the twelve default hooks, and
`detect` (`kl.py:684`) iterates it:

```python
skills = {
    hook: [n for n in installed if any(k in n.lower() for k in keywords)] if keywords else []
    for hook, keywords in HOOK_KEYWORDS.items()
}
```

The chain is never consulted. A chain naming `on.deploy.staging` gets no row, so
`init` — which is told to fill in every hook from detect's output — has nothing to
write, and the omission surfaces later as `validate_hooks` refusing to start.

### 4.2 The change

`detect` takes an optional `--chain PATH`, defaulting to the packaged
`chains/default.json`. It collects the hook names the chain's nodes actually list
in their `tasks`, and emits one row per hook.

For a hook present in `HOOK_KEYWORDS`, the curated keywords still apply — they
encode judgements a name cannot, such as `on.mr.open` matching `finishing` and
`on.implementation.start` matching `tdd`. `HOOK_KEYWORDS` is demoted from "the
list of hooks" to "overrides for the hooks we know about", and stays exactly as
it is.

For a hook absent from it, keywords are derived from the hook's own words:
split on `.` and `_`, then drop the structural tokens that carry no meaning about
what the node does — `on`, `requested`, `run`, `start`, `prepare`, `poll`, `open`,
`ready`. `on.deploy.staging` derives `{deploy, staging}` and matches an installed
`deploy-checklist` skill.

A derived keyword set that comes back empty (a hook whose every word is
structural) yields `[]`, which is already how `init` is told to handle "none":
write `kind: prompt` with a one-line instruction. The chain still runs.

### 4.3 Why not require every chain to ship its own keywords

Considered and rejected: a `keywords` field on the chain's task entries. It moves
the burden onto the chain author for a result the hook name already implies, and
it forks the chain schema — which §3 rules out. Derivation is wrong sometimes; a
wrong candidate list is a menu the human overrides, not a binding.

## 5. `ci_command` — the forge, detected rather than assumed

### 5.1 The defect

`skills/init/SKILL.md` states flatly that "`on.ci.poll` is `[gh, pr, checks]`".
This repo is the counter-example: `origin` is `git@gitlab.com:itsOmidKarami/kraft.git`,
`.gitlab-ci.yml` is present, `.github/workflows` is not, and `gh` — though
installed — has no pull requests to check here. The registry currently carries a
hand-written correction and a comment explaining it, which is the human doing
detect's job.

### 5.2 The change

`detect` returns `ci_command` beside `test_command`. Resolution order:

1. **Forge.** `git remote -v` on `origin`, corroborated by `.gitlab-ci.yml` versus
   `.github/workflows/`. A GitLab remote or a `.gitlab-ci.yml` means GitLab; a
   GitHub remote or a workflows directory means GitHub. When both are present,
   `origin`'s host wins — that is the forge whose CI a merge request runs on.
2. **CLI.** `shutil.which` for the corresponding client. GitLab → `glab`, and the
   command is `["glab", "ci", "status"]`. GitHub → `gh`, and the command is
   `["gh", "pr", "checks"]`.
3. **Neither.** `null`. `init` then writes `kind: prompt` for `on.ci.poll`, whose
   instruction is to report the pipeline's state by whatever means the human has.

`skills/init/SKILL.md` loses the hardcoded command and cites detect's `ci_command`
instead, keeping the existing rule that `on.test.run` and `on.ci.poll` are
`kind: subprocess` — but only when a command was found.

### 5.3 What this does not do

It does not verify that the CLI is authenticated, or that the remote has a
pipeline configured. `glab ci status` in an unauthenticated checkout fails at
dispatch with `glab`'s own message, which is clearer than anything a probe here
would produce, and probing costs a network round trip on every `detect`.

## 6. Registry validation — a key with a body

### 6.1 The defect

```python
HOOK_KEY = re.compile(r"^([a-z][a-z0-9_.]*):", re.MULTILINE)
```

`registry_hooks` (`kl.py:481`) returns the set of matches, and `validate_hooks`
subtracts it from the hooks the chain names. A registry containing

```yaml
on.ci.poll:
on.merge:
  kind: skill
```

passes `start` cleanly and fails at the `mr_checks` node, after six nodes have
run, with the `next` skill finding no `kind` to dispatch on.

The Kraft Lite design accepted this deliberately — a regex cannot see structure —
and `Kraft-l5z` records it as a known ceiling rather than a regression.

### 6.2 The change

The cheap upgrade the bead itself names: a key counts as bound only when the next
non-blank, non-comment line is indented. Implemented as a second pass over the
text rather than a cleverer single regex, because the lookahead needed to express
"followed by an indented line, but not by another column-zero key" is the kind of
regex someone decodes at 3am.

Comment-only bodies do not count. `on.ci.poll:` followed by a `# TODO` and then a
column-zero key is exactly the half-finished state this is meant to catch.

This stays a structural check, not a semantic one. A body of `kind: banana` still
passes validation and fails at dispatch; distinguishing those needs the YAML
parser §3 rules out, and the failure is immediate and legible when it comes.

## 7. `just lite-check` — the bump that was never published

### 7.1 The defect

The `lite-version` CI job (MR !51) refuses a `plugins/kraft-lite` change that does
not bump `plugin.json`. The opposite gap has no guard: a bumped version that never
reaches the public remote. Between !50 and the publish in that session, `main`
held `plugin.json` at `0.3.0` with no `kraft-lite--v0.3.0` tag anywhere, and
nothing said so.

### 7.2 Why not CI

`Kraft-mcx` proposes a default-branch job running `git ls-remote --tags lite`.
GitLab CI has no credentials for that GitHub remote — publishing runs from a
developer's machine — so the job needs a stored PAT to rotate. It would also cry
wolf: the window between "bump merges to main" and "publish runs" is a normal
state, not a defect, and a per-merge job fails main throughout it. A scheduled job
avoids the false alarm but still costs the token.

### 7.3 The change

A `just lite-check` recipe, run from the machine that already has the remote:
read the version from `plugin.json`, and

```
git ls-remote --exit-code --tags lite "kraft-lite--v$version"
```

Exit codes are read exactly as `lite-publish` already reads them at
`justfile:119-124`, and for the same reason: `--exit-code` says `2` for "no such
tag", and anything else is a remote that could not be reached, which must not be
reported as an answer. Here the polarity is inverted — `0` is the good case
(published), `2` is the finding (bumped but unpublished), anything else is an
error.

The recipe reports and exits non-zero on `2`. It is manual by design; §7.2 is the
argument that automation here buys a false-positive stream and a secret.

## 8. Manifest drift

`plugins/kraft-lite/.claude-plugin/marketplace.json` restates the plugin's `name`
and `description` inside its `plugins[0]` entry, duplicating
`.claude-plugin/plugin.json`. They agree today. Nothing makes them.

`Kraft-3oj` proposed replacing the recipe's tag construction with
`claude plugin tag`, which creates the same `{name}--v{version}` tag *and*
validates the manifests against each other. **That substitution is not
available.** `lite-publish` tags the subtree-split commit:

```
git subtree split --prefix=plugins/kraft-lite -b lite-publish
git tag "$tag" lite-publish
```

That commit is in a history disjoint from this repo's, created moments earlier and
deleted moments later. `claude plugin tag [path]` tags the monorepo commit at
`HEAD`. It cannot address `lite-publish`, so it cannot produce the tag that is
pushed.

What survives is the validation, and it does not need the CLI. A test asserts that
`marketplace.json`'s `plugins[0]` `name` and `description` equal `plugin.json`'s.
It runs inside the suite `lite-publish` already invokes at `justfile:110`, with no
network and no CLI dependency. The marketplace's own top-level `description` is
its shelf copy and is deliberately not pinned.

## 9. Files touched

| File | Change |
|---|---|
| `plugins/kraft-lite/kl.py` | `detect(--chain)`, derived keywords, `ci_command`, `HOOK_KEY` body check |
| `plugins/kraft-lite/skills/init/SKILL.md` | cite `ci_command`; drop the hardcoded `[gh, pr, checks]` |
| `plugins/kraft-lite/.claude-plugin/plugin.json` | version → `0.4.0` |
| `plugins/kraft-lite/tests/test_detect.py` | custom-chain hooks, each forge branch, CLI absent |
| `plugins/kraft-lite/tests/test_skills.py` | empty-body registry key, manifest drift |
| `justfile` | `lite-check` recipe |

The version bump is not optional: the `lite-version` job fails any
`plugins/kraft-lite` change that ships without one.

## 10. Testing

Test-first, per the registry's `on.implementation.start` binding. Each test must
be seen failing for the reason it names before the code that satisfies it exists —
a green-from-birth test here would pin nothing, since four of these five defects
are silent by construction.

- **`detect --chain`**: a fixture chain naming `on.deploy.staging` produces a row
  for it; the packaged chain still produces its twelve; a hook whose words are all
  structural produces `[]` rather than a missing key.
- **`ci_command`**: GitLab remote → `glab ci status`; GitHub remote → `gh pr checks`;
  neither CLI installed → `null`. `shutil.which` and the remote are both faked, so
  the suite does not depend on what is installed on the machine running it.
- **`HOOK_KEY`**: a registry with a bodied key validates; the same key with an
  empty body, and with a comment-only body, does not.
- **Manifest drift**: mutating either manifest's name or description fails.

`lite-check` gets no test. It is four lines of shell whose only logic is the exit
code table, and testing it means faking a git remote for less assurance than
running it once by hand.

## 11. Bead disposition

Closed on merge: `Kraft-1x2`, `Kraft-rje`, `Kraft-l5z`, `Kraft-mcx`, `Kraft-3oj`
— the last with a note recording that its `claude plugin tag` half is not
available and why, so the idea is not re-proposed.

Already closed at the spec node: `Kraft-asb`, `Kraft-b7p`.
