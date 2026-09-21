from __future__ import annotations

import atexit
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from kraft.executor import dispatch
from kraft.templates import Registry, load_registry

_SUPPORT = Path(__file__).parent
_REPO_ROOT = Path(__file__).resolve().parents[2]


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


def make_repo(tmp_path: Path, name: str = "sample") -> Path:
    dest = tmp_path / name
    shutil.copytree(_SUPPORT / "sample_repo", dest)
    _git(dest, "init", "-q", "-b", "main")
    _git(dest, "config", "user.email", "t@t")
    _git(dest, "config", "user.name", "t")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-m", "init")
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


_bd_template_dir: Path | None = None


def _bd_template() -> Path:
    """One `bd init`ed workspace per test-run process, built lazily and reused.

    `bd init` spins up Dolt (~3.6s); doing it per test dominated the suite. Every
    caller only needs a *working* bd workspace (adapters run `bd create`/`close`,
    tests run `bd show <id>` — no test inspects cross-issue state), so we init
    once and hand out `copytree` copies.
    """
    global _bd_template_dir
    if _bd_template_dir is None:
        tpl = Path(tempfile.mkdtemp(prefix="kraft-bd-tpl-"))
        _git(tpl, "init", "-q", "-b", "main")
        _git(tpl, "config", "user.email", "t@t")
        _git(tpl, "config", "user.name", "t")
        subprocess.run(
            ["bd", "init", "--prefix", "TEST"],
            cwd=tpl,
            capture_output=True,
            text=True,
            check=True,
        )
        atexit.register(shutil.rmtree, tpl, ignore_errors=True)
        _bd_template_dir = tpl
    return _bd_template_dir


def isolated_bd(tmp_path: Path, name: str = "tracker") -> Path:
    """A throwaway git repo with its own beads workspace. Return the repo path.

    `name` distinguishes a second workspace in the same `tmp_path` — an
    auto-intaken bead lives in its own repo's `.beads`, not the instance-wide
    tracker (Kraft-8mu.5.2)."""
    repo = tmp_path / name
    shutil.copytree(_bd_template(), repo)
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


def fake_registry(
    python_exe: str,
    fake_agent_path: Path,
    harnesses: dict[str, str] | None = None,
) -> Registry:
    """`harnesses` maps a hook point to the harness it should be bound to, for
    a test that needs more than one harness in a single chain. Unnamed hooks
    keep the shipped default (`claude`)."""
    base = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    hooks = dict(base.hooks)
    fake = f"{python_exe} {fake_agent_path}"
    hooks["on.implementation.start"] = {"kind": "agent", "command": fake}
    hooks[dispatch.JUDGE_HOOK] = {"kind": "agent", "command": fake}
    # The shipped registry binds these to `claude`. A test that drives the
    # default chain must not shell out to the operator's real agent, and a
    # missing binary would land the item in needs_human rather than at a gate.
    # on.review.local.run joined this list when it stopped being a noop
    # (Kraft-yenu) -- it is now a real `claude` skill hook like the others.
    for hook in (
        "on.spec.requested",
        "on.plan.requested",
        "on.chain.review_ready",
        "on.review.local.run",
    ):
        hooks[hook] = {**hooks[hook], "command": fake}
    # The shipped registry's back half is real now (Kraft-33j): four forge
    # hooks on `backend: auto`, and a human_review hook bound to the operator's
    # `claude`. A test driving the default chain must reach neither a forge CLI
    # nor a real agent, so they go back to noop here — the same reason the spec
    # and plan commands are swapped above.
    for hook in (
        "on.mr.describe",
        "on.mr.open",
        "on.mr.sync",
        "on.ci.poll",
        "on.merge",
        "on.merge.watch",
        "on.human_review.requested",
    ):
        hooks[hook] = {"kind": "builtin", "handler": "noop"}
    for hook, hid in (harnesses or {}).items():
        hooks[hook] = {**hooks[hook], "harness": hid}
    return Registry(hooks=hooks)


def fake_templates_dir(
    tmp_path: Path, agent_command: str, *, planning_hooks: bool = False, noop_verify: bool = False
) -> Path:
    """Registry + `quick-task`/`default` chains against a throwaway templates dir.

    `on.spec.requested`/`on.plan.requested` default to `builtin: noop` — most
    callers drive chains that never reach those gates and must not shell out.
    Pass `planning_hooks=True` to bind them to `agent_command` instead, the way
    `fake_registry` above does: spread the shipped binding and swap only the
    command, so its `skill:`/`artifact:` keys survive.

    `on.test.run` defaults to a real `python -m pytest -q` subprocess against
    the worktree. Pass `noop_verify=True` for a caller whose assertions are
    about an earlier node (e.g. pause/resume/rebase) and only needs the chain
    to *reach* completed — that subprocess is pure incidental cost there.

    `on.chain.review_ready` is always bound to `agent_command`, unlike spec and
    plan: `POST .../gates/chain_finalized/approve` reads and parses its
    artifact unconditionally now (Kraft-hm0), so a `noop` binding would leave
    every caller that approves that gate stuck at needs_human for a missing
    artifact. `fixtures/fake-claude.sh` answers with the chain's own unchanged
    tail, read back out of `chain_definition`, so a caller that never touches
    chain review still walks the rest of the chain exactly as before.
    """
    d = tmp_path / "templates"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "quick-task.yaml", d / "quick-task.yaml")
    shutil.copy(_REPO_ROOT / "templates" / "default.yaml", d / "default.yaml")
    # Only the file the shipped registry's `steering:` keys actually name, not
    # the whole real steering/ dir (its README.md is documentation, not a
    # steering file, and copying it in shows up as a phantom entry in every
    # steering-listing test). `exist_ok=True` on both: a caller that spins up
    # more than one `_client()` against the same `tmp_path` calls this twice.
    steering_dir = d / "steering"
    steering_dir.mkdir(exist_ok=True)
    shutil.copy(
        _REPO_ROOT / "templates" / "steering" / "never-signal-processes-you-didnt-start.md",
        steering_dir / "never-signal-processes-you-didnt-start.md",
    )

    def noop() -> dict:
        # A fresh dict per call, not one shared object: `yaml.safe_dump` aliases
        # repeated *identical objects* with `&id001`/`*id001` anchors, which would
        # make a GET/PUT round trip through JSON (which de-aliases) an unrelated
        # byte diff rather than a real one.
        return {"kind": "builtin", "handler": "noop"}

    shipped = load_registry(_REPO_ROOT / "templates" / "registry.yaml").hooks
    if planning_hooks:
        spec_hook = {**shipped["on.spec.requested"], "command": agent_command}
        plan_hook = {**shipped["on.plan.requested"], "command": agent_command}
    else:
        spec_hook = noop()
        plan_hook = noop()

    (d / "registry.yaml").write_text(
        yaml.safe_dump(
            {
                "hooks": {
                    "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                    "on.implementation.start": {"kind": "agent", "command": agent_command},
                    "on.repos.scan": {"kind": "builtin", "handler": "scan_submodules"},
                    "on.test.run": (
                        noop()
                        if noop_verify
                        else {"kind": "subprocess", "command": ["python", "-m", "pytest", "-q"]}
                    ),
                    "on.spec.requested": spec_hook,
                    "on.plan.requested": plan_hook,
                    "on.chain.review_ready": {
                        **shipped["on.chain.review_ready"],
                        "command": agent_command,
                    },
                    "on.review.local.run": noop(),
                    "on.mr.rebase": {"kind": "builtin", "handler": "mr_rebase"},
                    "on.mr.describe": noop(),
                    "on.mr.open": noop(),
                    "on.ci.poll": noop(),
                    "on.review.mr.run": noop(),
                    "on.mr_checks.repair": noop(),
                    "on.mr.sync": noop(),
                    "on.human_review.requested": noop(),
                    "on.merge": noop(),
                    "on.merge.watch": noop(),
                }
            }
        )
    )
    shutil.copy(_REPO_ROOT / "templates" / "policy.yaml", d / "policy.yaml")
    seed_v1_library(d, agent_command=agent_command)
    return d


def seed_v1_library(templates_dir: Path, *, agent_command: str | None = None) -> Path:
    """Put the shipped V1 layout -- `library.yaml` plus `chains/*.yaml` -- into
    `templates_dir`, beside whatever legacy files are already there.

    Beside, not instead: 5b owns converting the 60 callers that still read
    `registry.yaml` and the legacy chain files, so both layouts have to load
    out of one directory until then. They do not collide -- the V1 loader reads
    only `library.yaml` and `chains/`, and `load_templates` reads neither.

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
        # `true`. This is the same protection `noop_verify` gives the legacy
        # `on.test.run` binding, and it is not optional here: that builtin runs
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
        # And `claude` itself, because `escalate.dispatch` selects that harness by
        # name -- escalation is not a chain node, so nothing declares a harness
        # for it (see `escalate._ESCALATION_HARNESS`). Without this overlay every
        # test that reaches an escalation launched the real `claude` binary:
        # cheap only by accident, because `conftest._isolated_kraft_home` empties
        # `HOME` and CI has no API key, and a real agent turn on a developer
        # machine that does. `conftest._no_real_agent_binary` now refuses it, but
        # a refusal is not a fix -- the fix is that there is nothing left to
        # refuse.
        #
        # The *bundled* declaration with its `command:` swapped, not
        # `_FAKE_HARNESS`: escalation asks for `autocompact`, `permission_mode`
        # and `deny_tools`, which the minimal fake does not declare, and
        # `run_agent_task` raises on a capability a harness has not declared.
        # Swapping one line is what `escalate` naming a harness instead of a
        # command made possible.
        (harnesses / "claude.yaml").write_text(bundled.replace("command: [claude]", command))
        # Beside the library, and where dispatch reads it when no
        # `KRAFT_TEMPLATES_DIR` is set (`agent.harness_profile`) -- the same
        # two-directory split as the harness files above.
        write_harness_profiles(templates_dir, profiles)
        if home:
            write_harness_profiles(Path(home) / "templates", profiles)
    (templates_dir / "library.yaml").write_text(library)
    # KNOWN GAP, bridged here so a V1 fixture can actually run: a V1 steering
    # profile is *inline* in `library.yaml`, but `adapters/agent.py` still
    # resolves a task's `steering:` names to `templates/steering/<name>.md`
    # files -- nothing has wired the library's own `steering` mapping into
    # dispatch. Until that lands, write each profile out as the file the
    # dispatcher looks for, so the fixture exercises the chain rather than the
    # gap. Delete this the day dispatch reads `library.steering`.
    profiles = yaml.safe_load(library).get("steering") or {}
    if profiles:
        steering_dir = templates_dir / "steering"
        steering_dir.mkdir(exist_ok=True)
        for name, body in profiles.items():
            (steering_dir / f"{name}.md").write_text((body or {}).get("instructions", ""))
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


def v1_resolved(nodes: list[dict], *, chain_id: str = "t"):
    """A `ResolvedChain` over `nodes` (authored V1 node mappings) -- what
    `executor.intake` takes, and what `v1_chain` materializes."""
    from kraft.templates.models import Chain, ResolvedChain

    return ResolvedChain.from_chain(Chain.model_validate({"id": chain_id, "nodes": nodes}))


def v1_chain(nodes: list[dict], *, repo: Path | str, chain_id: str = "t"):
    """A `MaterializedChain` over `nodes` (authored V1 node mappings), bound to
    a single-repository target on `repo`."""
    from kraft.policy import InstancePolicy, InstancePolicyInput
    from kraft.templates.environment import Repository, WorkItemTarget

    return v1_resolved(nodes, chain_id=chain_id).materialize(
        target=WorkItemTarget.for_repository(Repository(id="target", path=str(repo))),
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
            # in a V1 walk reads it.
            chain_definition="{}",
            materialized_chain=chain.to_json(),
            **kwargs,
        )
    )


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
            registry=None,
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
