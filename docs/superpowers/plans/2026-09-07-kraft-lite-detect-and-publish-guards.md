# Kraft Lite detect and publish guards — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `kl.py detect` serve any chain on any forge, make registry validation catch an unbound hook at `start`, and put a guard on each half of the publish path.

**Architecture:** Four of the five changes land in `plugins/kraft-lite/kl.py` — `detect` gains a `--chain` argument and a `ci_command` field, and `registry_hooks` starts requiring a key to have a body. The remaining two are guards outside the module: a pytest that pins the two manifests to each other, and a `just` recipe that asks the public remote whether the current version was ever tagged.

**Tech Stack:** Python 3 stdlib only (`kl.py` ships inside a plugin that may be dropped into a repo with no packages installed — see the module docstring at `kl.py:1-10`), pytest for the plugin's own suite, `just` for the recipe.

**Spec:** `docs/superpowers/specs/2026-09-07-kraft-lite-detect-and-publish-guards-design.md`

**Chain:** `kl-1efa7d77` (node `implementation`, gate `implementation_approval`)

## Global Constraints

- **Stdlib only in `kl.py`.** No new imports beyond what is already at `kl.py:12-21`: `argparse`, `datetime`, `json`, `re`, `shutil`, `subprocess`, `sys`, `uuid`, `pathlib.Path`. `shutil` and `subprocess` are already there — Task 2 needs no new import.
- **No YAML parser.** The registry stays regex-and-line inspected. Spec §3.
- **`plugin.json` version must end at `0.4.0`.** The `lite-version` CI job (`.gitlab-ci.yml:102`) fails any `plugins/kraft-lite` change shipped without a bump. Task 5 does this; do not bump it twice.
- **Do not commit or push without the user's say-so.** `CLAUDE.md`'s conservative profile overrides this plan's commit steps: run them only when the user has approved that task's diff. Every commit step below is written assuming that approval exists.
- **Existing call sites must keep working.** `kl.detect(tmp_path)` is called positionally in `tests/test_detect.py` and from `kl.py:543`. Any new parameter is keyword-optional with a default.
- **Test command:** `uv run pytest plugins/kraft-lite/tests -q` from the repo root. The full `just test` is the Kraft backend suite (~14 min) and does not cover this plugin.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `plugins/kraft-lite/kl.py` | chain state + environment detection | 1, 2, 3 |
| `plugins/kraft-lite/skills/init/SKILL.md` | tells the agent how to write `registry.yaml` | 2 |
| `plugins/kraft-lite/tests/test_detect.py` | detection guesses | 1, 2 |
| `plugins/kraft-lite/tests/test_skills.py` | shipped-prose and manifest guards | 3, 4 |
| `plugins/kraft-lite/.claude-plugin/plugin.json` | version | 5 |
| `justfile` | `lite-check` recipe | 5 |

---

### Task 1: `detect --chain` emits a row per hook the chain names

Closes `Kraft-1x2`. Spec §4.

**Files:**
- Modify: `plugins/kraft-lite/kl.py:616-629` (add `HOOK_STOPWORDS` after `HOOK_KEYWORDS`), `kl.py:684-690` (`detect`), `kl.py:522` (argparse), `kl.py:542-544` (dispatch)
- Test: `plugins/kraft-lite/tests/test_detect.py`

**Interfaces:**
- Consumes: `DEFAULT_CHAIN` (`kl.py:266`), `HOOK_KEYWORDS` (`kl.py:616`), `_installed_skills(root) -> list[str]` (`kl.py:659`)
- Produces: `_hook_keywords(hook: str) -> tuple[str, ...]`; `detect(root: Path, chain_path: Path | None = None) -> dict` whose `skills` key is now ordered by the chain's own hook order

- [ ] **Step 1: Write the failing tests**

Append to `plugins/kraft-lite/tests/test_detect.py`:

```python
def _chain_file(tmp_path, *hooks):
    """A one-node chain naming exactly these hooks."""
    path = tmp_path / "custom.json"
    path.write_text(
        json.dumps(
            {
                "id": "custom",
                "loops": {},
                "nodes": [{"id": "only", "tasks": list(hooks), "gate_after": None}],
            }
        )
    )
    return path


def test_a_custom_hook_gets_candidates_from_its_own_words(tmp_path, monkeypatch):
    monkeypatch.setattr(kl, "_installed_skills", lambda root: ["acme:deploy-checklist", "other:unrelated"])
    got = kl.detect(tmp_path, _chain_file(tmp_path, "on.deploy.staging"))
    assert got["skills"] == {"on.deploy.staging": ["acme:deploy-checklist"]}


def test_a_curated_hook_keeps_its_curated_keywords(tmp_path, monkeypatch):
    # `finishing` is not a word in `on.mr.open`; only the curated table knows it.
    monkeypatch.setattr(kl, "_installed_skills", lambda root: ["superpowers:finishing-a-development-branch"])
    got = kl.detect(tmp_path, _chain_file(tmp_path, "on.mr.open"))
    assert got["skills"]["on.mr.open"] == ["superpowers:finishing-a-development-branch"]


def test_a_hook_of_only_structural_words_reports_no_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(kl, "_installed_skills", lambda root: ["anything:at-all"])
    got = kl.detect(tmp_path, _chain_file(tmp_path, "on.run.start"))
    assert got["skills"] == {"on.run.start": []}


def test_the_packaged_chain_still_reports_its_twelve_hooks(tmp_path):
    got = kl.detect(tmp_path)
    assert set(got["skills"]) == set(kl.HOOK_KEYWORDS)
    assert len(got["skills"]) == 12
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest plugins/kraft-lite/tests/test_detect.py -q`
Expected: FAIL. The first three with `TypeError: detect() takes 1 positional argument but 2 were given`; the fourth passes already (it describes today's behaviour and must keep passing).

- [ ] **Step 3: Add the stopword table and the keyword resolver**

Insert immediately after the `HOOK_KEYWORDS` dict closes at `kl.py:629`:

```python
#: Words in a hook name that say when it fires, not what it does. A custom hook
#: derives its candidates from the words that are left, so these must not match
#: a skill: `on.deploy.staging` should look for deploy skills, not for every
#: skill with "start" in its name.
HOOK_STOPWORDS = frozenset({"on", "requested", "run", "start", "prepare", "poll", "open", "ready"})


def _hook_keywords(hook: str) -> tuple[str, ...]:
    """Keywords to match installed skill names against.

    `HOOK_KEYWORDS` is the curated answer for the hooks that ship with the default
    chain — it encodes judgements the name alone cannot, like `on.mr.open` wanting
    `finishing`. It is not the list of hooks that exist: a custom chain names
    hooks nobody curated, and those derive from their own words.
    """
    if hook in HOOK_KEYWORDS:
        return HOOK_KEYWORDS[hook]
    return tuple(w for w in re.split(r"[._]", hook) if w and w not in HOOK_STOPWORDS)
```

- [ ] **Step 4: Rewrite `detect` to walk the chain**

Replace `detect` at `kl.py:684-690` with:

```python
def detect(root: Path, chain_path: Path | None = None) -> dict:
    chain = json.loads((chain_path or DEFAULT_CHAIN).read_text())
    # dict.fromkeys, not set: init writes the registry in this order, and a
    # registry whose keys follow the chain reads like the run it configures.
    hooks = dict.fromkeys(task for node in chain["nodes"] for task in node["tasks"])
    installed = _installed_skills(root)
    skills = {}
    for hook in hooks:
        keywords = _hook_keywords(hook)
        skills[hook] = [n for n in installed if any(k in n.lower() for k in keywords)]
    return {"test_command": _test_command(root), "skills": skills}
```

Note the dropped `if keywords else []`: `any()` over an empty keyword tuple is already `False`, so a curated `()` and a fully-structural name both yield `[]` without the special case.

- [ ] **Step 5: Wire the argument through the CLI**

At `kl.py:522`, replace `sub.add_parser("detect")` with:

```python
    detect_parser = sub.add_parser("detect")
    detect_parser.add_argument("--chain", type=Path, default=None)
```

At `kl.py:542-543`, replace the dispatch body with:

```python
    if args.verb == "detect":
        print(json.dumps(detect(root, args.chain), indent=2))
        return 0
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest plugins/kraft-lite/tests/test_detect.py -q`
Expected: PASS, all four new tests plus the five existing `test_command` tests.

- [ ] **Step 7: Check the CLI end to end**

Run: `python3 plugins/kraft-lite/kl.py detect | python3 -c "import json,sys;print(list(json.load(sys.stdin)['skills']))"`
Expected: the twelve `on.*` hooks, in chain order starting `on.spec.requested`.

- [ ] **Step 8: Commit**

```bash
git add plugins/kraft-lite/kl.py plugins/kraft-lite/tests/test_detect.py
git commit -m "kraft-lite: detect emits a row per hook the chain names (Kraft-1x2)"
```

---

### Task 2: `ci_command` detects the forge instead of assuming GitHub

Closes `Kraft-rje`. Spec §5.

**Files:**
- Modify: `plugins/kraft-lite/kl.py` (add `FORGE_CLI`, `_forge`, `_ci_command` beside `_test_command` at `kl.py:638-656`; add the field to `detect`'s return)
- Modify: `plugins/kraft-lite/skills/init/SKILL.md`
- Test: `plugins/kraft-lite/tests/test_detect.py`

**Interfaces:**
- Consumes: `detect(root, chain_path=None)` from Task 1
- Produces: `_forge(root: Path) -> str | None` returning `"gitlab"`, `"github"` or `None`; `_ci_command(root: Path) -> list[str] | None`; `detect(...)["ci_command"]`

- [ ] **Step 1: Write the failing tests**

Append to `plugins/kraft-lite/tests/test_detect.py`:

```python
def test_a_gitlab_repo_with_glab_installed_polls_with_glab(tmp_path, monkeypatch):
    (tmp_path / ".gitlab-ci.yml").write_text("stages: [test]\n")
    monkeypatch.setattr(kl.shutil, "which", lambda cli: f"/usr/bin/{cli}")
    assert kl.detect(tmp_path)["ci_command"] == ["glab", "ci", "status"]


def test_a_github_repo_with_gh_installed_polls_with_gh(tmp_path, monkeypatch):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    monkeypatch.setattr(kl.shutil, "which", lambda cli: f"/usr/bin/{cli}")
    assert kl.detect(tmp_path)["ci_command"] == ["gh", "pr", "checks"]


def test_the_origin_remote_outranks_a_stray_ci_file(tmp_path, monkeypatch):
    # A repo can carry a .github/workflows it no longer uses; origin is the forge
    # whose CI a merge request actually runs on.
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    monkeypatch.setattr(kl, "_origin_url", lambda root: "git@gitlab.com:me/x.git")
    monkeypatch.setattr(kl.shutil, "which", lambda cli: f"/usr/bin/{cli}")
    assert kl.detect(tmp_path)["ci_command"] == ["glab", "ci", "status"]


def test_a_forge_whose_cli_is_missing_reports_no_command(tmp_path, monkeypatch):
    (tmp_path / ".gitlab-ci.yml").write_text("stages: [test]\n")
    monkeypatch.setattr(kl.shutil, "which", lambda cli: None)
    assert kl.detect(tmp_path)["ci_command"] is None


def test_a_repo_with_no_forge_reports_no_command(tmp_path, monkeypatch):
    monkeypatch.setattr(kl, "_origin_url", lambda root: "")
    assert kl.detect(tmp_path)["ci_command"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest plugins/kraft-lite/tests/test_detect.py -q -k ci_command or forge`
Expected: FAIL with `KeyError: 'ci_command'` on the first two, and `AttributeError: module 'kl' has no attribute '_origin_url'` on the third and fifth.

- [ ] **Step 3: Write the forge detection**

Insert directly after `_test_command` ends at `kl.py:656`:

```python
#: The client each forge's CI is read with, and the command that reads it once.
#: Both are read-only status calls: `next` is forbidden to sit in a wait loop.
FORGE_CLI = {
    "gitlab": ("glab", ["glab", "ci", "status"]),
    "github": ("gh", ["gh", "pr", "checks"]),
}


def _origin_url(root: Path) -> str:
    """`origin`'s URL, or empty when there is no remote, no git, or no repo."""
    try:
        done = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _forge(root: Path) -> str | None:
    """Which forge's CI this repo's merge requests run on.

    `origin` decides, because that is where a merge request lands. The CI files
    are only the fallback for a checkout with no remote configured yet — and a
    repo can carry a `.github/workflows` it has stopped using.
    """
    url = _origin_url(root)
    if "gitlab" in url:
        return "gitlab"
    if "github" in url:
        return "github"
    if (root / ".gitlab-ci.yml").is_file():
        return "gitlab"
    if (root / ".github" / "workflows").is_dir():
        return "github"
    return None


def _ci_command(root: Path) -> list[str] | None:
    """None when the forge is unknown or its client is not installed. init then
    writes `kind: prompt`, which is honest: a bound command that cannot run reads
    as finished and fails at the node instead."""
    forge = _forge(root)
    if forge is None:
        return None
    cli, command = FORGE_CLI[forge]
    return command if shutil.which(cli) else None
```

- [ ] **Step 4: Add the field to `detect`'s return**

In `detect` (rewritten in Task 1), change the final line to:

```python
    return {
        "test_command": _test_command(root),
        "ci_command": _ci_command(root),
        "skills": skills,
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest plugins/kraft-lite/tests/test_detect.py -q`
Expected: PASS.

- [ ] **Step 6: Stop `init` hardcoding the GitHub command**

In `plugins/kraft-lite/skills/init/SKILL.md`, replace the rule reading

> `on.test.run` and `on.ci.poll` are `kind: subprocess`. Use the detected test command; `on.ci.poll` is `[gh, pr, checks]`.

with:

```markdown
- `on.test.run` and `on.ci.poll` are `kind: subprocess` — but only when detect
  found a command for them. Use `test_command` and `ci_command` verbatim. Either
  one being `null` means `kind: prompt` instead: for `on.ci.poll`, an instruction
  to report the pipeline's state once, by whatever means this repo has. Never
  write a command detect did not report — a binding that cannot run here reads as
  finished until the node fails.
```

- [ ] **Step 7: Verify the detected command matches this repo**

Run: `python3 plugins/kraft-lite/kl.py detect | python3 -c "import json,sys;print(json.load(sys.stdin)['ci_command'])"`
Expected: `['glab', 'ci', 'status']` — this repo's `origin` is `gitlab.com` and `glab` is installed. This is the case `Kraft-rje` was filed against; getting `['gh', 'pr', 'checks']` here means the fix did not take.

- [ ] **Step 8: Run the whole plugin suite**

Run: `uv run pytest plugins/kraft-lite/tests -q`
Expected: PASS. `test_skills.py` reads every SKILL.md, so a malformed edit in Step 6 surfaces here.

- [ ] **Step 9: Commit**

```bash
git add plugins/kraft-lite/kl.py plugins/kraft-lite/tests/test_detect.py plugins/kraft-lite/skills/init/SKILL.md
git commit -m "kraft-lite: detect the forge instead of assuming gh (Kraft-rje)"
```

---

### Task 3: a registry key counts as bound only with a body

Closes `Kraft-l5z`. Spec §6.

**Files:**
- Modify: `plugins/kraft-lite/kl.py:478-486` (`HOOK_KEY`, `registry_hooks`)
- Test: `plugins/kraft-lite/tests/test_skills.py`

**Interfaces:**
- Consumes: `HOOK_KEY` (`kl.py:478`), `STATE_DIR`
- Produces: `_bound_hooks(text: str) -> set[str]`; `registry_hooks` keeps its signature `(root: Path) -> set[str] | None` and its `None`-when-absent contract, so `validate_hooks` (`kl.py:489`) needs no change

- [ ] **Step 1: Write the failing tests**

Append to `plugins/kraft-lite/tests/test_skills.py`:

```python
def test_a_key_with_a_body_is_bound():
    assert kl._bound_hooks("on.merge:\n  kind: skill\n  skill: x:y\n") == {"on.merge"}


def test_a_key_with_no_body_is_not_bound():
    # The Kraft-l5z case: this passed validation and died at dispatch, six nodes in.
    assert kl._bound_hooks("on.ci.poll:\non.merge:\n  kind: skill\n") == {"on.merge"}


def test_a_comment_only_body_is_not_bound():
    text = "on.ci.poll:\n# TODO: pick a client\non.merge:\n  kind: skill\n"
    assert kl._bound_hooks(text) == {"on.merge"}


def test_a_trailing_key_with_no_body_is_not_bound():
    assert kl._bound_hooks("on.merge:\n  kind: skill\non.ci.poll:\n") == {"on.merge"}


def test_blank_lines_between_a_key_and_its_body_do_not_unbind_it():
    assert kl._bound_hooks("on.merge:\n\n  kind: skill\n") == {"on.merge"}
```

`test_skills.py` does **not** import `kl` today — it only reads files. Add the preamble below the existing `import pytest` at `tests/test_skills.py:11`, matching how `tests/test_detect.py:12-16` does it:

```python
import sys

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import kl  # noqa: E402
```

`PLUGIN` is already defined at `test_skills.py:13`; keep the existing line and do not add a second one — insert only the `sys` import, the `sys.path.insert` call below that existing `PLUGIN` assignment, and the `kl` import.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest plugins/kraft-lite/tests/test_skills.py -q -k bound`
Expected: FAIL with `AttributeError: module 'kl' has no attribute '_bound_hooks'`.

- [ ] **Step 3: Write the body check**

Replace `registry_hooks` at `kl.py:481-486` with:

```python
def _bound_hooks(text: str) -> set[str]:
    """Keys that are actually bound, not merely present.

    A regex cannot express "followed by an indented line, but not by another
    column-zero key" without a lookahead nobody wants to read at 3am, so this
    walks the lines instead. Structural only: `kind: banana` is bound as far as
    this is concerned, and fails at dispatch with a message that says so.
    """
    bound: set[str] = set()
    pending: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = HOOK_KEY.match(line)
        if match:
            # A key immediately following another leaves the first unbound.
            pending = match.group(1)
        elif pending and line[:1] in " \t":
            bound.add(pending)
            pending = None
    return bound


def registry_hooks(root: Path) -> set[str] | None:
    """The hooks bound in this repo's registry, or None when there is no registry."""
    path = root / STATE_DIR / "registry.yaml"
    if not path.is_file():
        return None
    return _bound_hooks(path.read_text())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest plugins/kraft-lite/tests/test_skills.py -q -k bound`
Expected: PASS, five tests.

- [ ] **Step 5: Verify this repo's own registry still validates**

Run: `python3 -c "
import sys; sys.path.insert(0, 'plugins/kraft-lite')
import kl, json
from pathlib import Path
bound = kl.registry_hooks(Path('.'))
chain = json.loads(kl.DEFAULT_CHAIN.read_text())
wanted = {t for n in chain['nodes'] for t in n['tasks']}
print('missing:', sorted(wanted - bound))
"`
Expected: `missing: []`. This repo's `.kraft-lite/registry.yaml` binds all twelve with bodies; anything else means the line walk is rejecting valid entries.

- [ ] **Step 6: Run the whole plugin suite**

Run: `uv run pytest plugins/kraft-lite/tests -q`
Expected: PASS. `test_walk_end_to_end.py` starts chains and would fail if validation got stricter than intended.

- [ ] **Step 7: Commit**

```bash
git add plugins/kraft-lite/kl.py plugins/kraft-lite/tests/test_skills.py
git commit -m "kraft-lite: an unbound registry key fails at start, not mid-walk (Kraft-l5z)"
```

---

### Task 4: pin `marketplace.json` to `plugin.json`

Closes `Kraft-3oj`. Spec §8.

**Files:**
- Test: `plugins/kraft-lite/tests/test_skills.py`

**Interfaces:**
- Consumes: `PLUGIN` (already defined at the top of `test_skills.py`)
- Produces: nothing importable; this task is a guard only

- [ ] **Step 1: Write the failing test**

Append to `plugins/kraft-lite/tests/test_skills.py`:

```python
def test_the_marketplace_entry_matches_the_plugin_manifest():
    """`claude plugin tag` would validate this, but it tags HEAD and lite-publish
    tags the subtree-split commit, so the CLI cannot be used here. The two files
    restate each other's name and description with nothing pinning them."""
    plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    marketplace = json.loads((PLUGIN / ".claude-plugin" / "marketplace.json").read_text())
    entry = next(p for p in marketplace["plugins"] if p["source"] == "./")
    assert entry["name"] == plugin["name"]
    assert entry["description"] == plugin["description"]
```

`json` is already imported at `test_skills.py:7` and `PLUGIN` defined at `test_skills.py:13`; this test needs no new imports. It does not need `kl`, so it stands whether or not Task 3 has landed.

- [ ] **Step 2: Run the test to verify it fails for the right reason**

The manifests currently agree, so this test is green from birth and pins nothing until proven. Break it deliberately first:

```bash
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("plugins/kraft-lite/.claude-plugin/marketplace.json")
d = json.loads(p.read_text())
d["plugins"][0]["description"] = "drifted"
p.write_text(json.dumps(d, indent=2) + "\n")
PY
uv run pytest plugins/kraft-lite/tests/test_skills.py -q -k marketplace_entry
```

Expected: FAIL on the description assertion. Then restore: `git checkout plugins/kraft-lite/.claude-plugin/marketplace.json`

- [ ] **Step 3: Run the test to verify it passes**

Run: `uv run pytest plugins/kraft-lite/tests/test_skills.py -q -k marketplace_entry`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add plugins/kraft-lite/tests/test_skills.py
git commit -m "kraft-lite: pin the marketplace entry to the plugin manifest (Kraft-3oj)"
```

---

### Task 5: `just lite-check`, and the version bump

Closes `Kraft-mcx`. Spec §7 and §9.

**Files:**
- Modify: `justfile` (new recipe immediately before `lite-publish` at `justfile:105`)
- Modify: `plugins/kraft-lite/.claude-plugin/plugin.json` (`"version": "0.3.0"` → `"0.4.0"`)

**Interfaces:**
- Consumes: the `lite` git remote, `plugins/kraft-lite/.claude-plugin/plugin.json`
- Produces: `just lite-check`, exit 0 when published, 1 otherwise

- [ ] **Step 1: Add the recipe**

Insert into `justfile` directly above the `lite-publish` recipe:

```make
# Has the version in plugin.json actually been published? `lite-version` in CI
# stops a plugin change without a bump; this is the other half — a bump that
# never reached the remote. Deliberately manual: CI has no credentials for the
# lite remote, and the window between a bump merging and a publish running is a
# normal state a per-merge job would fail main throughout.
lite-check:
    #!/usr/bin/env bash
    set -euo pipefail
    version=$(python3 -c "import json;print(json.load(open('plugins/kraft-lite/.claude-plugin/plugin.json'))['version'])")
    tag="kraft-lite--v$version"
    # Same exit-code reading as lite-publish, opposite polarity: there, an existing
    # tag means the bump is missing; here, a missing tag means the publish is.
    # Anything but 0 or 2 is a remote that could not be reached, which must not
    # read as an answer either way.
    rc=0; git ls-remote --exit-code --tags lite "$tag" >/dev/null || rc=$?
    case $rc in
        0) echo "kraft-lite v$version is published as $tag" ;;
        2) echo "kraft-lite v$version has no $tag on the lite remote — run 'just lite-publish'"; exit 1 ;;
        *) echo "cannot reach the lite remote: git ls-remote exited $rc"; exit 1 ;;
    esac
```

- [ ] **Step 2: Verify it reports the published state**

Run: `just lite-check`
Expected: `kraft-lite v0.3.0 is published as kraft-lite--v0.3.0`, exit 0. The tag exists on the remote today.

- [ ] **Step 3: Bump the version**

In `plugins/kraft-lite/.claude-plugin/plugin.json`, change `"version": "0.3.0"` to `"version": "0.4.0"`.

- [ ] **Step 4: Verify it now reports the finding**

Run: `just lite-check`
Expected: `kraft-lite v0.4.0 has no kraft-lite--v0.4.0 on the lite remote — run 'just lite-publish'`, exit 1. This is the state `Kraft-mcx` was filed about, now visible.

- [ ] **Step 5: Confirm the bump satisfies the CI guard**

Run: `git diff --name-only main -- plugins/kraft-lite | head` and confirm `.claude-plugin/plugin.json` is among the changed files.
Expected: it is. The `lite-version` job compares the published surface against the target branch and needs this file in the diff.

- [ ] **Step 6: Run the whole plugin suite one last time**

Run: `uv run pytest plugins/kraft-lite/tests -q`
Expected: PASS, all tests including the four tasks' additions.

- [ ] **Step 7: Commit**

```bash
git add justfile plugins/kraft-lite/.claude-plugin/plugin.json
git commit -m "kraft-lite: lite-check catches a bump that was never published (Kraft-mcx)"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §4 `detect --chain`, derived keywords | 1 |
| §5 `ci_command`, forge order, init SKILL.md | 2 |
| §6 `HOOK_KEY` body requirement | 3 |
| §7 `just lite-check` | 5 |
| §8 manifest drift test | 4 |
| §9 version → `0.4.0` | 5 |
| §10 testing (all four bullets) | 1, 2, 3, 4 |
| §11 bead closure | post-merge, see below |

§5.3 (no auth probe) and §10's "`lite-check` gets no test" are decisions not to build; no task needed.

**Placeholder scan:** none. Every code step carries the literal text to insert.

**Type consistency:** `detect(root, chain_path=None)` is defined in Task 1 and extended in Task 2 — Task 2's Step 4 edits the return statement Task 1's Step 4 wrote, so the tasks must run in order. `_origin_url` is monkeypatched in Task 2 Step 1 and defined in Task 2 Step 3, same task. `_bound_hooks` is defined and used within Task 3. `HOOK_KEYWORDS` keeps its shape; `_hook_keywords` reads it.

**Ordering constraint:** Tasks 1 → 2 are sequential (same function). Tasks 3, 4, 5 are independent of each other and of 1–2, except that Task 5's suite run in Step 6 assumes the rest have landed.

**Bead closure** happens at the chain's `merge` node, not in a task: `Kraft-1x2`, `Kraft-rje`, `Kraft-l5z`, `Kraft-mcx`, and `Kraft-3oj` — the last with a note recording that its `claude plugin tag` half is not available and why.
