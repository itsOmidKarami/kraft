from __future__ import annotations

import atexit
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_SUPPORT = Path(__file__).parent
_REPO_ROOT = Path(__file__).resolve().parents[2]


#: Agent CLIs this suite must never actually launch (tests/conftest.py refuses
#: them). Kraft-jxu39: the only reason a stray real launch has been cheap so far
#: is that `_isolated_kraft_home` redirects `HOME` to an empty temp dir and this
#: machine has no `ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN`, so the binary
#: resolves and exits in milliseconds. On a developer machine with a key set the
#: same call is a real agent turn: network, tokens, tens of seconds. Here, not in
#: the conftest, so a test can import it without `import conftest`, which is
#: ambiguous now the repo root has a conftest.py too.
REAL_AGENT_BINARIES = frozenset({"claude", "codex", "gemini", "amp", "cursor-agent"})


#: Commits are made at a fixed time so that building the same tree twice gives
#: the same SHA. A commit hash covers its own timestamp at one-second
#: granularity, so two `make_repo()` calls either side of a second boundary used
#: to produce different SHAs — a ~7% flake in any test comparing them, and worse
#: under load, because contention widens the gap between the two calls.
_FIXED_DATE = "2026-01-01T00:00:00+00:00"


def _git(cwd: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": _FIXED_DATE, "GIT_COMMITTER_DATE": _FIXED_DATE}
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )


_repo_template: Path | None = None


def make_repo(tmp_path: Path, name: str = "sample") -> Path:
    """A committed copy of `sample_repo`. Built once per process and copied,
    the way `isolated_bd` is: five git spawns a call were ~15% of the suite's
    git (Kraft-qmhfc). Commits use `_FIXED_DATE`, so a copy carries the same
    SHAs a fresh build would, and its index is refreshed, so it reads clean."""
    global _repo_template
    if _repo_template is None:
        tpl = Path(tempfile.mkdtemp(prefix="kraft-repo-tpl-")) / "sample"
        shutil.copytree(_SUPPORT / "sample_repo", tpl)
        _git(tpl, "init", "-q", "-b", "main")
        _git(tpl, "config", "user.email", "t@t")
        _git(tpl, "config", "user.name", "t")
        _git(tpl, "add", "-A")
        _git(tpl, "commit", "-m", "init")
        atexit.register(shutil.rmtree, tpl.parent, ignore_errors=True)
        _repo_template = tpl
    dest = tmp_path / name
    shutil.copytree(_repo_template, dest, symlinks=True)
    # The copy's index still carries the template's stat data (inode, mtime),
    # so plumbing that does not refresh -- `git diff-index HEAD` -- would call
    # every tracked file modified. A fresh build would not.
    _git(dest, "update-index", "-q", "--refresh")
    return dest


def make_repo_with_submodule(
    tmp_path: Path, *, submodule_path: str = "repos/pkg"
) -> tuple[Path, Path]:
    """A root repo with one real submodule already added and committed.

    Every test in the submodule-merge-requests plan that needs "a superproject
    plus a submodule" builds on this rather than repeating `git submodule add`
    -- the shape that broke on work item 9d0ab38ff3c9439b90506df0f6966660.
    """
    sub = make_repo(tmp_path, name="pkg")
    root = make_repo(tmp_path, name="ws")
    _git(root, "-c", "protocol.file.allow=always", "submodule", "add", str(sub), submodule_path)
    _git(root, "commit", "-m", "add submodule")
    return root, sub


def make_repo_with_engineering(tmp_path: Path, files: dict[str, str], name: str = "sample") -> Path:
    """make_repo(), then add repo-relative `files` (path -> text), commit, return the repo."""
    dest = make_repo(tmp_path, name)
    for rel, text in files.items():
        fp = dest / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(text)
    _git(dest, "add", "-A")
    _git(dest, "commit", "-m", "add engineering docs")
    return dest


_bd_templates: dict[bool, Path] = {}

#: Whether `isolated_bd` hands out a real `bd init`ed workspace. `True` for any
#: caller outside pytest (frontend/e2e/serve.py); `tests/conftest.py` turns it
#: off for every test the in-memory beads fake covers, so those never pay for
#: `bd init` or need `bd` installed (Kraft-qmhfc).
REAL_BD = True


def _bd_template(real: bool) -> Path:
    """One workspace template per test-run process, built lazily and reused.

    `bd init` spins up Dolt (~3.6s); doing it per test dominated the suite. Every
    caller only needs a *working* bd workspace, so we init once and hand out
    `copytree` copies. The fake's template is a git repo with an empty `.beads/`,
    which is all Kraft itself looks for (search's repo filter, doctor).
    """
    if real not in _bd_templates:
        tpl = Path(tempfile.mkdtemp(prefix="kraft-bd-tpl-"))
        _git(tpl, "init", "-q", "-b", "main")
        _git(tpl, "config", "user.email", "t@t")
        _git(tpl, "config", "user.name", "t")
        if real:
            subprocess.run(
                ["bd", "init", "--prefix", "TEST"],
                cwd=tpl,
                capture_output=True,
                text=True,
                check=True,
            )
        else:
            (tpl / ".beads").mkdir()
        atexit.register(shutil.rmtree, tpl, ignore_errors=True)
        _bd_templates[real] = tpl
    return _bd_templates[real]


def isolated_bd(tmp_path: Path, name: str = "tracker") -> Path:
    """A throwaway git repo with its own beads workspace. Return the repo path.

    `name` distinguishes a second workspace in the same `tmp_path` — an
    auto-intaken bead lives in its own repo's `.beads`, not the instance-wide
    tracker (Kraft-8mu.5.2)."""
    repo = tmp_path / name
    shutil.copytree(_bd_template(REAL_BD), repo)
    return repo


def fake_docker_bin(tmp_path: Path) -> Path:
    """A directory holding a `docker` that unwraps `docker run [OPTIONS] IMAGE
    CMD...` back to `CMD...` and execs it — proves the wrap shape a real
    sandbox launch produces without a real daemon. `-u`/`-v`/`-w`/`-e`/`--name`
    each consume exactly one following argument in what `sandbox.docker_argv`
    emits, so skipping flag+value pairs generically finds the image (the
    first survivor) and the real command (everything after it), regardless
    of exact flag count or order. `--security-opt=...`/`--cap-drop=...` carry
    their value in the same token (`=`-joined), so they are dropped outright
    rather than skip-one'd.

    Also answers `docker rm -f NAME` -- the container teardown
    `sandbox.teardown` issues once a sandboxed session's client side is down
    -- by appending `NAME` to `$FAKE_DOCKER_RM_LOG` when that env var is set,
    so a test can tell the real teardown call happened without a daemon to
    actually ask.
    """
    bin_dir = tmp_path / "fake-docker-bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        '# Test seam: touch a sentinel if asked, so a test can tell "the real\n'
        '# command ran because it went through this fake docker" apart from\n'
        '# "the real command ran because nothing wrapped it at all" -- the two\n'
        "# look identical from the marker file the real command itself writes.\n"
        'if [ -n "${FAKE_DOCKER_CALLED:-}" ]; then : > "$FAKE_DOCKER_CALLED"; fi\n'
        'if [ "$1" = "rm" ]; then\n'
        "  shift\n"
        '  for arg in "$@"; do\n'
        '    [ "$arg" = "-f" ] && continue\n'
        '    if [ -n "${FAKE_DOCKER_RM_LOG:-}" ]; then echo "$arg" >> "$FAKE_DOCKER_RM_LOG"; fi\n'
        "  done\n"
        "  exit 0\n"
        "fi\n"
        'shift # drop "run"\n'
        'image=""\n'
        "cmd=()\n"
        "skip=0\n"
        'for arg in "$@"; do\n'
        '  if [ "$skip" = 1 ]; then skip=0; continue; fi\n'
        '  case "$arg" in\n'
        "    --rm|--security-opt=*|--cap-drop=*) continue ;;\n"
        "    -u|-v|-w|-e|--name) skip=1; continue ;;\n"
        "    *)\n"
        '      if [ -z "$image" ]; then image="$arg"; else cmd+=("$arg"); fi\n'
        "      ;;\n"
        "  esac\n"
        "done\n"
        'exec "${cmd[@]}"\n'
    )
    docker.chmod(0o755)
    return bin_dir


def fake_templates_dir(tmp_path: Path, agent_command: str) -> Path:
    """A throwaway templates dir holding the shipped V1 layout -- `library.yaml`,
    `chains/`, `harnesses.yaml` and `policy.yaml` -- with every agent profile
    launching `agent_command` (`seed_v1_library`). The steering file the seed
    names is copied in too, and nothing else of the real steering dir: its
    README is documentation, not a steering file."""
    d = tmp_path / "templates"
    d.mkdir(parents=True, exist_ok=True)
    steering_dir = d / "steering"
    steering_dir.mkdir(exist_ok=True)
    shutil.copy(
        _REPO_ROOT / "templates" / "steering" / "never-signal-processes-you-didnt-start.md",
        steering_dir / "never-signal-processes-you-didnt-start.md",
    )
    shutil.copy(_REPO_ROOT / "templates" / "policy.yaml", d / "policy.yaml")
    seed_v1_library(d, agent_command=agent_command)
    return d


def seed_v1_library(templates_dir: Path, *, agent_command: str | None = None) -> Path:
    """Put the shipped V1 layout -- `library.yaml` plus `chains/*.yaml` -- into
    `templates_dir`.

    The *shipped* seed, copied rather than hand-written, so a fixture cannot
    drift from the chain an operator actually gets.
    """
    templates_dir.mkdir(parents=True, exist_ok=True)
    library = (_REPO_ROOT / "templates" / "library.yaml").read_text()
    shipped_profiles = yaml.safe_load((_REPO_ROOT / "templates" / "harnesses.yaml").read_text())
    if agent_command is None:
        write_harness_profiles(templates_dir, shipped_profiles["harnesses"])
    else:
        # The library keeps its real `harness:` ids -- `codex_default`,
        # `claude_review` -- and only the *profiles* they name change: each is
        # put on the overlaid `fake` provider, which launches `agent_command`,
        # its `executable:` dropped so the provider's own command stands. The
        # shipped `defaults:` stay, so they reach the launch as they would in
        # production. `fake` and `claude` are profiles too, for the hand-built
        # chains that name them. Nothing rewrites a task's `harness:` any more:
        # that rewrite is how the suite ran a library the product never ships
        # (Task 5e).
        profiles = {
            id: {k: v for k, v in body.items() if k != "executable"} | {"provider": "fake"}
            for id, body in shipped_profiles["harnesses"].items()
        }
        profiles |= {"fake": {"provider": "fake"}, "claude": {"provider": "claude"}}
        # And the `kraft.verify_changed_test_scopes` builtin becomes an inert
        # `true`. Not optional: that builtin runs
        # **the connected repo's own `test_command`**, and several tests in this
        # suite connect a repo declaring `pytest` or `just test`. A V1 chain
        # reaching it in a unit test runs this suite inside itself --
        # `builtins.run_setup_command`/`_subprocess.run_task` have no timeout,
        # so the nested run finishes the whole suite before the outer one
        # continues. A test that means to exercise the real builtin builds its
        # own chain (tests/executor/test_dispatch.py) and is untouched by this.
        parsed = yaml.safe_load(library)
        for task in parsed.get("tasks", {}).values():
            if task.get("kind") == "builtin":
                task.clear()
                task.update({"kind": "subprocess", "command": "true"})
        library = yaml.safe_dump(parsed, sort_keys=False)
        # `$KRAFT_HOME/templates/harnesses`, which is what
        # `paths.default_harnesses_dir()` reads -- *not* `templates_dir`, which
        # is `KRAFT_TEMPLATES_DIR` and a different directory under pytest
        # (`conftest._isolated_kraft_home` pins `KRAFT_HOME` to its own path).
        # Writing it beside the library instead meant every V1 walk driven
        # through `support.api._client` stopped at "harness 'fake' is not
        # available", which reads as a chain defect and is a fixture one.
        home = os.environ.get("KRAFT_HOME")
        harnesses = (Path(home) / "templates" if home else templates_dir) / "harnesses"
        harnesses.mkdir(parents=True, exist_ok=True)
        # `fake` is the *bundled* `claude` declaration under another id with its
        # `command:` swapped -- the fake agents stand in for `claude`, and speak
        # its stream-json, so a task on `fake` must read their usage envelope and
        # rate-limit events exactly the way a real `claude` launch is read. A
        # minimal declaration without `structured_log`/`rate_limit_signal` turned
        # every rate limit into a plain failure and every cost into zero.
        bundled = (_REPO_ROOT / "src" / "kraft" / "harnesses" / "claude.yaml").read_text()
        command = f"command: {json.dumps(shlex.split(agent_command))}"
        (harnesses / "fake.yaml").write_text(
            bundled.replace("id: claude", "id: fake").replace("command: [claude]", command)
        )
        # And `claude` itself, for a hand-built chain or profile that names the
        # provider directly: without this overlay it would launch the real
        # `claude` binary -- cheap only by accident, because
        # `conftest._isolated_kraft_home` empties `HOME` and CI has no API key.
        # `conftest._no_real_agent_binary` refuses it, but a refusal is not a
        # fix -- the fix is that there is nothing left to refuse. (An escalation
        # turn runs on the `claude_review` profile, `escalate.ESCALATION_TASK`,
        # which the profiles above already put on `fake`.)
        #
        # The *bundled* declaration with its `command:` swapped, not
        # `_FAKE_HARNESS`: escalation asks for `autocompact`, `permission_mode`
        # and `deny_tools`, which the minimal fake does not declare, and
        # `run_agent_task` raises on a capability a harness has not declared.
        (harnesses / "claude.yaml").write_text(bundled.replace("command: [claude]", command))
        # Beside the library, and where dispatch reads it when no
        # `KRAFT_TEMPLATES_DIR` is set (`agent.harness_profile`) -- the same
        # two-directory split as the harness files above.
        write_harness_profiles(templates_dir, profiles)
        if home:
            write_harness_profiles(Path(home) / "templates", profiles)
    (templates_dir / "library.yaml").write_text(library)
    chains = templates_dir / "chains"
    chains.mkdir(exist_ok=True)
    for chain in sorted((_REPO_ROOT / "templates" / "chains").glob("*.yaml")):
        shutil.copy(chain, chains / chain.name)
    return templates_dir


def write_harness_profiles(templates_dir: Path, profiles: dict) -> None:
    """Merge `profiles` (id -> `harnesses.yaml` body) into
    `templates_dir/harnesses.yaml`, keeping any profile already there that
    `profiles` does not name -- two fixtures seeding one home must not undo
    each other."""
    path = Path(templates_dir) / "harnesses.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = (yaml.safe_load(path.read_text()) or {}) if path.is_file() else {}
    merged = {**(existing.get("harnesses") or {}), **profiles}
    path.write_text(yaml.safe_dump({"harnesses": merged}, sort_keys=False))


def harness_without(capability: str, harness_id: str = "claude"):
    """The bundled harness set with `harness_id` stripped of `capability`: a
    harness shape no shipped YAML has, for pinning what a launch refuses.

    `run(harnesses=harness_without("permission_mode"), allowed_tools=(), ...)`"""
    from dataclasses import replace

    from kraft import harness

    hs = harness.load(None)
    h = hs.valid[harness_id]
    caps = {k: v for k, v in h.capabilities.items() if k != capability}
    return replace(hs, valid={**hs.valid, harness_id: replace(h, capabilities=caps)})


def v1_library(templates_dir: Path, *, agent_command: str = "true"):
    """The `TemplateLibrary` for `templates_dir`, seeding the V1 layout first
    if it is not already there.

    **The seed it writes is always a neutered one.** `seed_v1_library` only
    puts the library's harness profiles on the `fake` provider and neuters the
    `kraft.verify_changed_test_scopes` builtin when it is given an
    `agent_command`; seeded without one, `codex_default` is the shipped profile
    and the builtin is real, so a test that dispatched it would launch the
    operator's `codex` and run this suite inside itself. Nothing was
    walking such a chain when this defaulted to `None`, but `v1_named_chain`
    below is about to be the door ~156 call sites go through, and a helper that
    many callers adopt has to be safe by default rather than safe by accident.

    `true` rather than a real fake agent: it makes an agent task launchable and
    harmless. A caller whose assertions are *about* the agent seeds the
    directory itself first -- `fake_templates_dir` does, and this only seeds a
    directory that has no `library.yaml` yet, so passing one through is
    unchanged.
    """
    from kraft.templates.library import TemplateLibrary

    if not (Path(templates_dir) / "library.yaml").is_file():
        seed_v1_library(Path(templates_dir), agent_command=agent_command)
    return TemplateLibrary.from_yaml_dir(templates_dir)


def v1_named_chain(
    templates_dir: Path, chain_id: str = "quick-task", *, agent_command: str = "true"
):
    """The `ResolvedChain` for one *shipped* chain, resolved out of
    `templates_dir` (seeded if it is not already).

    `executor.entry.intake` takes a `ResolvedChain`, and the legacy callers it
    replaces named a template by string -- overwhelmingly `"quick-task"`, which
    is why that is the default. Deliberately resolved from the test's own
    templates directory rather than the packaged one: the dir `v1_library` seeds
    has every harness profile on the `fake` provider and the
    `verify_changed_test_scopes` builtin neutered, where a chain resolved from
    the packaged tree would launch a real agent and run this suite inside
    itself.

    `agent_command` is what every agent task launches, and is only read when
    `templates_dir` is not seeded yet (see `v1_library`): a caller whose
    assertions are about the agent's work passes the fake agent here.
    """
    return v1_library(templates_dir, agent_command=agent_command).resolve_chain(chain_id)


def v1_seeded_chain(templates_dir: Path, nodes: list[dict], *, agent_command: str, chain_id="t"):
    """`nodes` (authored V1 node mappings) as a `ResolvedChain`, after seeding
    `templates_dir` so the `fake` harness an agent task names launches
    `agent_command` (see `seed_v1_library`)."""
    seed_v1_library(Path(templates_dir), agent_command=agent_command)
    return v1_resolved(nodes, chain_id=chain_id)


def v1_fix_loop_node(node_id: str, measure: dict, *, judge: bool = True) -> dict:
    """The legacy `verify_fix_loop` shape as one V1 node: `measure` is the
    node's task, the fix loop's one task is an agent on the `fake` harness
    (legacy: `on.implementation.start`), and -- unless `judge=False` -- an
    agent judge on the same harness (legacy: `on.fix_loop.judge`). Its loop
    key is `f"{node_id}.fix_loop"` (`walk._loop_key`)."""
    fix_loop: dict = {
        "tasks": [{"id": "fix", "kind": "agent", "harness": "fake", "prompt": "Fix it."}]
    }
    if judge:
        fix_loop["judge"] = {
            "id": "judge",
            "kind": "agent",
            "harness": "fake",
            "prompt": "Decide whether another repair attempt is justified.",
        }
    return {"id": node_id, "kind": "exec", "tasks": [measure], "fix_loop": fix_loop}


def e2e_templates_dir(tmp_path: Path) -> Path:
    return fake_templates_dir(tmp_path, "claude --model claude-haiku-4-5-20251001")


# --- Template Schema V1 -------------------------------------------------------
#
# A V1 work item's whole input is its `materialized_chain` column, so a test
# that drives the executor builds one directly: `intake.py` does not write the
# column yet (Task 5), and there is deliberately no legacy fallback to walk.


def v1_resolved(
    nodes: list[dict] | str, *, chain_id: str = "t", steering: dict[str, str] | None = None
):
    """A `ResolvedChain` over `nodes` (authored V1 node mappings, or the same
    list as YAML text) -- what `executor.intake` takes, and what `v1_chain`
    materializes. `steering` is the profile text a library would have resolved
    (`ResolvedChain.steering`)."""
    from kraft.templates.models import Chain, ResolvedChain

    if isinstance(nodes, str):
        nodes = yaml.safe_load(nodes)
    return ResolvedChain.from_chain(
        Chain.model_validate({"id": chain_id, "nodes": nodes}), steering=steering
    )


def v1_chain(nodes, *, repo, chain_id: str = "t", steering: dict | None = None, target=None):
    """A `MaterializedChain` bound to `target`, else to one repository, `repo`.

    `nodes` is authored V1 node mappings, the same list as YAML text, or an
    already-resolved `ResolvedChain` (e.g. `v1_named_chain(...)` for a shipped
    chain). A `MaterializedChain` passes through unchanged."""
    from kraft.policy import InstancePolicy, InstancePolicyInput
    from kraft.templates.environment import WorkItemTarget
    from kraft.templates.models import MaterializedChain, ResolvedChain

    if isinstance(nodes, MaterializedChain):
        return nodes
    resolved = (
        nodes
        if isinstance(nodes, ResolvedChain)
        else v1_resolved(nodes, chain_id=chain_id, steering=steering)
    )
    return resolved.materialize(
        target=target or WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput.model_validate({})),
    )


def v1_item(database, chain, *, repo: Path | str, wid: str = "w1", title: str = "t", **kwargs):
    """Insert a work item whose only chain is `chain`. Returns the awaitable
    `database.write` gives back, so callers `await` it like `mk_item`."""
    from kraft import store

    return database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title=title,
            repo=str(repo),
            chain_template=chain.chain.id,
            # Not `""`: the column is NOT NULL and Task 5 removes it. Nothing
            # in a V1 *walk* reads it -- true of the executor only.
            # `progress.py` is called from the API layer, not the executor,
            # and reads `materialized_chain` for the board's "Task N of M".
            chain_definition="{}",
            materialized_chain=chain.to_json(),
            **kwargs,
        )
    )


@dataclass
class Item:
    """One V1 work item in `database`, as `make_item` filed it. The readbacks
    most executor tests assert on, and `session` to seed a worker session."""

    database: Any
    run_dirs: Any
    id: str
    chain: Any
    repo: Path

    @property
    def worktree(self) -> Path:
        return self.run_dirs.worktrees / self.id

    def row(self) -> dict:
        return dict(
            self.database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (self.id,)).fetchone()
            )
        )

    def status(self) -> str:
        return self.row()["status"]

    def events(self, type: str | None = None) -> list[dict]:
        from kraft import events

        evts = self.database.read(lambda c: events.read_after(c, 0, self.id))
        return [dict(e) for e in evts if type is None or e["type"] == type]

    def sessions(self, node: str | None = None) -> list[dict]:
        """This item's worker_sessions rows, oldest first, optionally one node's."""
        rows = self.database.read(
            lambda c: c.execute(
                "SELECT * FROM worker_sessions WHERE work_item_id = ? ORDER BY created_at",
                (self.id,),
            ).fetchall()
        )
        return [dict(r) for r in rows if node is None or r["node_id"] == node]

    async def session(
        self,
        sid: str,
        path: str,
        status: str | None = None,
        *,
        node: str | None = None,
        running: tuple[int, float] | None = None,
        **kwargs,
    ):
        """Seed one worker session through the real store writers.

        `path` is the task path (`"implementation.main.implement"`), which is
        also the session's hook point; its node is the path's first segment.
        A path with no dots (`"escalation"`) runs on `node`, or the item's
        current node. `running=(pid, start_time)` marks it running;
        `status` then exits it (`"done"`, `"failed"`, ...). `kwargs` go to
        `store.create_session` (`round`, `head_sha`, `thread`, `command`).
        Returns what `create_session` returns: `(id, log_path, result_path)`.
        """
        from kraft import store

        node_id = node or (path.split(".")[0] if "." in path else self.row()["current_node_id"])
        created = await self.database.write(
            lambda c: store.create_session(
                c,
                id=sid,
                work_item_id=self.id,
                node_id=node_id,
                hook_point=path,
                log_path=str(self.run_dirs.logs / f"{sid}.log"),
                result_path=str(self.run_dirs.results / f"{sid}.json"),
                **kwargs,
            )
        )
        if running is not None:
            await self.database.write(lambda c: store.session_running(c, sid, *running))
        if status is not None:
            await self.database.write(lambda c: store.session_exited(c, sid, status))
        return created


async def make_item(
    database,
    run_dirs,
    chain,
    node: str | None = None,
    *,
    repo: Path | str,
    wid: str = "w1",
    worktree: bool = False,
    target=None,
    **item_kwargs,
) -> Item:
    """A V1 work item on `chain`, standing at `node`. Tests take the `item_on`
    fixture (tests/conftest.py), which supplies `database`, `run_dirs` and a
    `repo`, and call it as `await item_on(chain, "verify")`.

    - `chain`: authored node mappings, YAML text of the same list, a
      `ResolvedChain` (`v1_named_chain(...)` for a shipped one) or a
      `MaterializedChain` -- anything `v1_chain` takes.
    - `node`: loads the chain at that node and enters it (`load_chain` +
      `enter_node`, the events a walk writes). `None` leaves the item where
      intake leaves it: no current node.
    - `worktree=True` creates the (empty) worktree directory, for code that
      only checks it exists.
    - `target`: its `WorkItemTarget` (`support.workspace.workspace_target`).
    - `item_kwargs` go to `store.create_work_item` (`status`, `title`, ...).

    The item has no bead (`bead_id` NULL): filing one is `executor.intake`'s
    job, and a test about the bead should use that.
    """
    from kraft import store

    materialized = v1_chain(chain, repo=repo, target=target)
    await v1_item(database, materialized, repo=repo, wid=wid, **item_kwargs)
    if node is not None:
        await database.write(lambda c: store.load_chain(c, wid, node))
        await database.write(lambda c: store.enter_node(c, wid, node))
    it = Item(database, run_dirs, wid, materialized, Path(repo))
    if worktree:
        it.worktree.mkdir(parents=True, exist_ok=True)
    return it


#: A harness definition for the fake agent: the same shape `src/kraft/harnesses/
#: claude.yaml` declares (so `fixtures/fake_agent.py` sees the flags it already
#: parses), with `command` pointed at the fake and `usage` read from the result
#: file so no envelope has to be faked.
_FAKE_HARNESS = """
id: fake
kind: cli
command: {command}
capabilities:
  prompt:          {{ cli: ["-p", "{{value}}"] }}
  context:         {{ channel: system_prompt, cli: ["--append-system-prompt", "{{value}}"] }}
  model:           {{ cli: ["--model", "{{value}}"] }}
  effort:          {{ cli: ["--effort", "{{value}}"] }}
  resume:          {{ cli: ["--resume", "{{value}}"] }}
  usage:           {{ source: result_file }}
"""


def fake_harness_home(tmp_path: Path, command: list[str], *, harness_id: str = "fake") -> Path:
    """A `$KRAFT_HOME` whose `templates/harnesses/` overlays one harness that
    launches `command`. Set `KRAFT_HOME` to the returned path and an agent task
    selecting `harness_id` runs the fake instead of a real CLI."""
    home = tmp_path / "kraft-home"
    directory = home / "templates" / "harnesses"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{harness_id}.yaml").write_text(
        _FAKE_HARNESS.format(command=json.dumps([str(c) for c in command])).replace(
            "id: fake", f"id: {harness_id}"
        )
    )
    # A task selects a *profile*, so the harness needs one of the same id.
    write_harness_profiles(home / "templates", {harness_id: {"provider": harness_id}})
    return home


async def v1_walk(
    tmp_path: Path,
    chain,
    *,
    repo: Path | str,
    repo_entry: dict | None = None,
    policy=None,
    wid: str = "w1",
    title: str = "t",
    steer: str | None = None,
    run_dirs=None,
    start_index: int = 0,
    start_step: int = 0,
    **item_kwargs,
):
    """File `chain` as one work item and walk it once.

    Returns `(status, events, sessions, row)` -- the readbacks nearly every
    assertion about a walk needs, as plain dicts, with the database already
    closed. A caller that needs more reads the run directory itself.
    """
    from kraft import db as _db
    from kraft import events as _events
    from kraft import executor
    from kraft.executor.context import LaunchContext
    from kraft.paths import RunDirs

    rd = run_dirs or RunDirs(tmp_path / "run").ensure()
    database = await _db.Database.open(rd.db)
    try:
        await v1_item(database, chain, repo=repo, wid=wid, title=title, **item_kwargs)
        status = await executor.run_once(
            database,
            rd,
            work_item_id=wid,
            policy=policy,
            steer=steer,
            start_index=start_index,
            start_step=start_step,
            # `setup_command: ""` is the repo declaring it needs no preparation;
            # a repo entry without one refuses to cut a worktree at all.
            launch=LaunchContext(
                repo_entry={"setup_command": ""} if repo_entry is None else repo_entry,
                steering_dir=None,
            ),
        )
        evts = [dict(e) for e in database.read(lambda c: _events.read_after(c, 0, wid))]
        sessions = [
            dict(r)
            for r in database.read(
                lambda c: c.execute(
                    "SELECT * FROM worker_sessions WHERE work_item_id = ? ORDER BY created_at",
                    (wid,),
                ).fetchall()
            )
        ]
        row = dict(
            database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
        )
        return status, evts, sessions, row
    finally:
        await database.close()


def fails_once(real):
    """`real`, except that its first call raises `RuntimeError("poison tick")`
    -- one bad tick of a loop that must survive it. Usage:

        monkeypatch.setattr(store, "session_progress", fails_once(store.session_progress))
    """
    calls = []

    def once(*a, **kw):
        calls.append(a)
        if len(calls) == 1:
            raise RuntimeError("poison tick")
        return real(*a, **kw)

    return once
