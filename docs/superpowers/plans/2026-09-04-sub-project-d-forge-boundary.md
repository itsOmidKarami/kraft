# Forge Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardwired `gitlab_project` field with a neutral `forge` + `project` pair, so a GitHub repo can be connected before any forge plugin is written.

**Architecture:** A host-to-forge lookup table in `config.probe_repo` replaces the `"gitlab" in remote` string match. `config.load_repos` gains a normalization pass that reads legacy `gitlab_project` entries as `forge: gitlab`, so no file on disk needs migrating. The API models and the Settings readout follow.

**Tech Stack:** Python 3.14, FastAPI, PyYAML, pytest; React 18 + TypeScript + Vitest.

**Spec:** `docs/superpowers/specs/2026-09-04-sub-project-d-forge-boundary-design.md`

**Beads:** `Kraft-8mu.1` (parent `Kraft-8mu`)

## Global Constraints

- Python 3.14+. Unparenthesized multi-exception `except A, B:` (PEP 758) is used in this codebase and is correct — do not "fix" it.
- `$KRAFT_HOME/templates/` is seeded once and **never overwritten**. No task may rewrite a user's `repos.yaml` as an upgrade step.
- `probe_repo` is read-only: it never writes inside a target repo, and an undeterminable field returns `None` rather than raising.
- `forge` is a free string. Unknown values must load without error — no enum, no validation.
- This plan ships **no forge client**. `on.mr.open`, `on.ci.poll`, `on.review.mr.run` and `on.merge` stay bound to `builtins.noop`.
- Run `just lint` before each commit; the repo is ruff-checked and format-checked.

---

### Task 1: Forge detection in `probe_repo`

**Files:**
- Modify: `src/kraft/config.py:129-148`
- Test: `tests/test_settings_api.py:288-291`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `config.probe_repo(path) -> dict` whose returned mapping has keys `forge: str | None` and `project: str | None` and **no** `gitlab_project` key. Module-level `config._FORGES: dict[str, str]` mapping hostname to forge name.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_settings_api.py`, replacing the two existing lines at 290-291:

```python
import subprocess

from kraft import config
from support.harness import make_repo


def _set_origin(repo, url):
    subprocess.run(["git", "remote", "add", "origin", url], cwd=repo, check=True)


def test_probe_detects_no_forge_without_remote(tmp_path):
    repo = make_repo(tmp_path)
    probed = config.probe_repo(repo)
    assert probed["forge"] is None
    assert probed["project"] is None
    assert "gitlab_project" not in probed


def test_probe_detects_gitlab_ssh(tmp_path):
    repo = make_repo(tmp_path)
    _set_origin(repo, "git@gitlab.com:group/repo.git")
    probed = config.probe_repo(repo)
    assert probed["forge"] == "gitlab"
    assert probed["project"] == "group/repo"


def test_probe_detects_gitlab_https(tmp_path):
    repo = make_repo(tmp_path)
    _set_origin(repo, "https://gitlab.com/group/sub/repo.git")
    probed = config.probe_repo(repo)
    assert probed["forge"] == "gitlab"
    assert probed["project"] == "group/sub/repo"


def test_probe_detects_github_ssh(tmp_path):
    repo = make_repo(tmp_path)
    _set_origin(repo, "git@github.com:owner/repo.git")
    probed = config.probe_repo(repo)
    assert probed["forge"] == "github"
    assert probed["project"] == "owner/repo"


def test_probe_detects_github_https(tmp_path):
    repo = make_repo(tmp_path)
    _set_origin(repo, "https://github.com/owner/repo")
    probed = config.probe_repo(repo)
    assert probed["forge"] == "github"
    assert probed["project"] == "owner/repo"


def test_probe_unknown_host_is_not_an_error(tmp_path):
    repo = make_repo(tmp_path)
    _set_origin(repo, "git@git.example.com:team/repo.git")
    probed = config.probe_repo(repo)
    assert probed["forge"] is None
    assert probed["project"] is None
    assert probed["name"] == "sample"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test -k probe_detects`
Expected: FAIL — `KeyError: 'forge'`.

- [ ] **Step 3: Write the implementation**

In `src/kraft/config.py`, add above `probe_repo`:

```python
#: Hostnames Kraft can recognize in an origin URL. Self-hosted instances have
#: arbitrary hostnames and no read-only signal, so they are set by hand in
#: repos.yaml instead.
_FORGES = {
    "gitlab.com": "gitlab",
    "github.com": "github",
}


def _detect_forge(remote: str) -> tuple[str | None, str | None]:
    """(forge, project) from an origin URL, or (None, None). Never raises."""
    for host, forge in _FORGES.items():
        if host in remote:
            tail = remote.split(host, 1)[-1].lstrip(":/")
            return forge, (tail.removesuffix(".git") or None)
    return None, None
```

Replace lines 129-134 (the `gitlab_project` block) with:

```python
    remote = _git(root, "remote", "get-url", "origin") or ""
    forge, project = _detect_forge(remote)
```

And in the returned dict, replace `"gitlab_project": gitlab_project,` with:

```python
        "forge": forge,
        "project": project,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test -k probe`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/config.py tests/test_settings_api.py
git commit -m "feat: detect forge and project from origin instead of GitLab only"
```

---

### Task 2: Tolerant `repos.yaml` reader

**Files:**
- Modify: `src/kraft/config.py:62-72`
- Test: `tests/test_settings_api.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `config.load_repos(path) -> list[dict]` where every returned repo has `forge` and `project` keys and never a `gitlab_project` key, regardless of what is on disk. `config.save_repos` is unchanged in signature and writes whatever it is given.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings_api.py`:

```python
def test_load_repos_reads_legacy_gitlab_project(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r", "gitlab_project": "group/repo"}]})
    (repo,) = config.load_repos(path)
    assert repo["forge"] == "gitlab"
    assert repo["project"] == "group/repo"
    assert "gitlab_project" not in repo


def test_load_repos_passes_through_new_shape(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r", "forge": "github", "project": "o/r"}]})
    (repo,) = config.load_repos(path)
    assert repo["forge"] == "github"
    assert repo["project"] == "o/r"


def test_load_repos_new_shape_wins_over_legacy(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(
        path,
        {"repos": [{"path": "/r", "forge": "github", "project": "o/r", "gitlab_project": "g/r"}]},
    )
    (repo,) = config.load_repos(path)
    assert repo["forge"] == "github"
    assert repo["project"] == "o/r"
    assert "gitlab_project" not in repo


def test_load_repos_defaults_both_to_none(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r"}]})
    (repo,) = config.load_repos(path)
    assert repo["forge"] is None
    assert repo["project"] is None


def test_load_repos_keeps_unknown_forge(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r", "forge": "gitea", "project": "t/r"}]})
    (repo,) = config.load_repos(path)
    assert repo["forge"] == "gitea"


def test_save_repos_round_trip_drops_legacy_key(tmp_path):
    path = tmp_path / "repos.yaml"
    config.write_yaml(path, {"repos": [{"path": "/r", "gitlab_project": "group/repo"}]})
    config.save_repos(path, config.load_repos(path))
    assert "gitlab_project" not in path.read_text()
    assert "forge: gitlab" in path.read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test -k load_repos`
Expected: FAIL — `KeyError: 'forge'` on the first case.

- [ ] **Step 3: Write the implementation**

In `src/kraft/config.py`, replace the body of `load_repos` after the existing `path` validation loop:

```python
def load_repos(path: str | Path) -> list[dict]:
    data = read_yaml(path, REPOS_DEFAULT)
    repos = data.get("repos") or []
    if not isinstance(repos, list) or not all(isinstance(r, dict) for r in repos):
        raise ConfigError("repos.yaml: 'repos' must be a list of mappings")
    for r in repos:
        if not isinstance(r.get("path"), str) or not r["path"]:
            raise ConfigError("repos.yaml: every repo needs a string 'path'")
        _normalize_forge(r)
    return repos
```

And add above it:

```python
def _normalize_forge(repo: dict) -> None:
    """Read a pre-forge repos.yaml entry in place.

    `templates/` is seeded once and never overwritten, so compatibility for the
    `gitlab_project` -> `forge`/`project` rename lives in the reader rather than
    in a migration that would rewrite a file the user owns.
    """
    legacy = repo.pop("gitlab_project", None)
    if repo.get("forge") is None and legacy:
        repo["forge"] = "gitlab"
        repo["project"] = legacy
    repo.setdefault("forge", None)
    repo.setdefault("project", None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test -k "load_repos or save_repos"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/config.py tests/test_settings_api.py
git commit -m "feat: read legacy gitlab_project entries as forge/project"
```

---

### Task 3: API models

**Files:**
- Modify: `src/kraft/api.py:947-957` (`RepoBody`), `src/kraft/api.py:988-996` (`add_repo` entry), `src/kraft/api.py:1001-1008` (`RepoPatch`)
- Test: `tests/test_settings_api.py`

**Interfaces:**
- Consumes: `config.probe_repo` from Task 1 (keys `forge`, `project`); `config.load_repos` from Task 2.
- Produces: `POST /repos` and `PATCH /repos` accept `forge: str | None` and `project: str | None`; `GET /repos` returns them. No endpoint accepts or returns `gitlab_project`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_settings_api.py` (follow the existing client fixture used by the other `/repos` tests in this file):

```python
def test_add_repo_stores_probed_forge(client, tmp_path):
    repo = make_repo(tmp_path, name="ghrepo")
    _set_origin(repo, "git@github.com:owner/repo.git")
    r = client.post("/repos", json={"path": str(repo)})
    assert r.status_code == 201
    assert r.json()["forge"] == "github"
    assert r.json()["project"] == "owner/repo"
    assert "gitlab_project" not in r.json()


def test_patch_repo_overrides_forge(client, tmp_path):
    repo = make_repo(tmp_path, name="patchrepo")
    client.post("/repos", json={"path": str(repo)})
    r = client.patch(f"/repos?path={repo}", json={"forge": "gitea", "project": "t/r"})
    assert r.status_code == 200
    (entry,) = [x for x in client.get("/repos").json()["repos"] if x["path"] == str(repo)]
    assert entry["forge"] == "gitea"
    assert entry["project"] == "t/r"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test -k "add_repo_stores_probed_forge or patch_repo_overrides_forge"`
Expected: FAIL — response has no `forge` key.

- [ ] **Step 3: Write the implementation**

In `src/kraft/api.py`, in `RepoBody` replace `gitlab_project: str | None = None` with:

```python
    forge: str | None = None
    project: str | None = None
```

In `RepoPatch`, make the identical replacement.

In `add_repo`, replace the `"gitlab_project": body.gitlab_project or probed["gitlab_project"],` entry line with:

```python
        "forge": body.forge or probed["forge"],
        "project": body.project or probed["project"],
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test -k settings_api`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/api.py tests/test_settings_api.py
git commit -m "feat: forge and project on the repos API"
```

---

### Task 4: Settings readout

**Files:**
- Modify: `frontend/src/types.ts:204-223`, `frontend/src/views/Settings.tsx:211-217`
- Test: `frontend/src/views/Settings.test.tsx`

**Interfaces:**
- Consumes: the `GET /repos` and `POST /repos/probe` shapes from Task 3.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing test**

Find the existing `Settings.test.tsx` case asserting the GitLab readout and replace it with:

```tsx
it("shows the detected forge and project", async () => {
  renderProbe({ forge: "github", project: "owner/repo" });
  expect(await screen.findByText("github · owner/repo")).toBeInTheDocument();
});

it("says so when no forge was detected", async () => {
  renderProbe({ forge: null, project: null });
  expect(await screen.findByText("no forge remote detected")).toBeInTheDocument();
});
```

Reuse whatever probe-rendering helper the neighbouring cases in this file already use; `renderProbe` above stands for that helper, not a new one.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run Settings`
Expected: FAIL — the old "no GitLab remote" text renders instead.

- [ ] **Step 3: Write the implementation**

In `frontend/src/types.ts`, in both `Repo` and `RepoProbe`, replace `gitlab_project: string | null;` with:

```ts
  forge: string | null;
  project: string | null;
```

In `frontend/src/views/Settings.tsx` replace the GitLab field block:

```tsx
        <div className="field">
          <label>
            Forge project <span className="field-hint">· on.mr.open / on.ci.poll / on.merge</span>
          </label>
          <div className="input readout">
            {probe?.forge ? `${probe.forge} · ${probe.project}` : "no forge remote detected"}
          </div>
        </div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test-ui`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/views/Settings.tsx frontend/src/views/Settings.test.tsx
git commit -m "feat: forge-neutral repo readout in Settings"
```

---

### Task 5: Sweep and close

**Files:**
- Modify: any remaining occurrence found by the sweep.

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: the acceptance guarantee — no `gitlab` identifier outside `_FORGES` and test fixtures.

- [ ] **Step 1: Sweep**

Run:

```bash
grep -rni "gitlab" src/kraft/ frontend/src/
```

Expected: four hits, all in `src/kraft/config.py`, all required —

| Line | Why it must stay |
|---|---|
| `_FORGES` table entry | the host-to-forge lookup itself |
| `_normalize_forge` popping `gitlab_project` | spec §3's tolerant reader has to name the legacy key |
| `_normalize_forge` assigning `forge = "gitlab"` | spec §3's first table row |
| that function's docstring | explains the rename it implements |

Anything *else* — a leftover type, a stale comment, a test fixture — gets fixed now. Do not edit `docs/`; the design documents describe the GitLab plugin and stay as they are.

- [ ] **Step 2: Full gates**

Run: `just test && just test-ui && just lint`
Expected: all pass.

- [ ] **Step 3: Commit and close the bead**

```bash
git add -A
git commit -m "chore: remove remaining GitLab-specific naming"
bd close Kraft-8mu.1 --reason="forge boundary landed; GitHub connectable, no forge client yet"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 Repo identity (`forge` + `project`, unvalidated) | 1, 2 |
| §2 Probe (host table, best-effort, self-hosted out of scope) | 1 |
| §3 Reading old files (reader not migration, all four rows of the table) | 2 |
| §4 API and UI (models, readout stays a readout) | 3, 4 |
| §5 Testing (probe matrix, normalization matrix, round-trip, POST case, UI case) | 1, 2, 3, 4 |
| §6 Deferred plugin selection | no task — deliberately out of scope |
| Acceptance: no `gitlab` outside `_FORGES` | 5 |

**Placeholders:** none. Every code step carries the code. Task 4 Step 1 names `renderProbe` as a stand-in for the file's existing helper and says so explicitly rather than inventing one.

**Type consistency:** `forge` and `project` are `str | None` in Python and `string | null` in TypeScript in every task. `_detect_forge` returns `tuple[str | None, str | None]` and is consumed only inside `probe_repo`. `_normalize_forge` mutates in place and returns `None`, matching its single call site inside the `load_repos` loop.
