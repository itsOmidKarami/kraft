from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from kraft import skill as _skill
from kraft import steering as _steering
from kraft.paths import default_skills_dir

_VALID_KINDS = {"builtin", "agent", "subprocess", "forge"}
_FORGE_HANDLERS = {"open_mr", "sync_mr", "ci_poll", "merge"}
#: Duplicated in `adapters.forge.resolve`, deliberately: config validation must
#: not import the adapter layer. Edit both together. `auto` is the exception —
#: `adapters.forge.backend_for` translates it to one of the others at dispatch,
#: so `resolve` never sees it.
_FORGE_BACKENDS = {"auto", "glab", "gh", "fake"}
# Config files that share the templates directory but are not chain templates.
# One definition: `load_templates` skips them, and the registry save copies the
# templates around them. Without this, every settings file the UI writes would be
# read as a malformed template and show up as degraded health.
CONFIG_FILES = frozenset(
    {
        "registry.yaml",
        "policy.yaml",
        "repos.yaml",
        "access.yaml",
        "notify.yaml",
        "intake.yaml",
    }
)
# The complete gate set. Public because the API validates approve/reject against it
# and the chain-review skill documents it — a second copy is how those drift apart.
GATE_NAMES = {"spec_approval", "plan_approval", "chain_finalized", "human_review_approval"}
#: What an intake attachment stands in for (Kraft-dgh). Keyed on the gate rather
#: than the node id: gate names are a validated closed vocabulary, node ids are
#: free text a custom template chooses.
ATTACHMENT_GATES = {"spec": "spec_approval", "plan": "plan_approval"}
#: An `artifact:` value becomes a path segment (`.engineering/<kind>s/<id>.md`),
#: so it is a bare lowercase identifier — not a path, not a pattern.
_ARTIFACT_KIND = re.compile(r"[a-z][a-z0-9_-]*")
#: The levels `claude --effort` takes. Checked at config load rather than at
#: dispatch: a typo reaching the CLI fails the node *after* the work item has
#: already paid for a worktree and a session (Kraft-tff).
_EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}

#: The modes `claude --permission-mode` takes. Checked at config load for the
#: same reason as `_EFFORT_LEVELS`: a typo in a permission grant that reaches
#: the CLI fails the node after the work item has paid for a worktree and a
#: session.
_PERMISSION_MODES = {"default", "auto", "acceptEdits", "plan", "bypassPermissions"}


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class Registry:
    hooks: dict


@dataclass(frozen=True)
class Template:
    id: str
    nodes: list


@dataclass(frozen=True)
class TemplateSet:
    valid: dict
    invalid: dict


def _agent_profiles() -> dict:
    # Function-local: a config loader that imports the adapter layer at module
    # scope invites a cycle later, even though there is none today.
    from kraft.adapters.agent import PROFILES

    return PROFILES


def load_registry(
    path: str | Path,
    *,
    steering_dir: Path | None = None,
    skills_dir: Path | None = None,
) -> Registry:
    path = Path(path)
    steering_dir = steering_dir if steering_dir is not None else path.parent / "steering"
    # Not `path.parent / "skills"`: a method file is shipped in the package and
    # only *overlaid* from $KRAFT_HOME, so the default is the home directory,
    # not a sibling of whichever registry file is being validated.
    skills_dir = skills_dir if skills_dir is not None else default_skills_dir()
    try:
        data = yaml.safe_load(path.read_text())
    # ValueError covers the UnicodeDecodeError `read_text()` raises on a file
    # that is not UTF-8: it is a ValueError, not an OSError.
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise RegistryError(f"{path.name}: cannot read/parse: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        raise RegistryError(f"{path.name}: expected a top-level 'hooks' mapping")
    for hook, binding in data["hooks"].items():
        if not isinstance(binding, dict) or "kind" not in binding:
            raise RegistryError(f"{path.name}: hook {hook!r} is missing 'kind'")
        kind = binding["kind"]
        if kind not in _VALID_KINDS:
            raise RegistryError(f"{path.name}: hook {hook!r} has unknown kind {kind!r}")
        if kind == "builtin" and not isinstance(binding.get("handler"), str):
            raise RegistryError(f"{path.name}: builtin hook {hook!r} needs a string 'handler'")
        if kind == "agent" and not isinstance(binding.get("command"), str):
            raise RegistryError(f"{path.name}: agent hook {hook!r} needs a string 'command'")
        if kind == "forge":
            handler = binding.get("handler")
            if handler not in _FORGE_HANDLERS:
                raise RegistryError(
                    f"{path.name}: forge hook {hook!r} has unknown handler {handler!r}; "
                    f"known: {sorted(_FORGE_HANDLERS)}"
                )
            backend = binding.get("backend")
            if backend not in _FORGE_BACKENDS:
                raise RegistryError(
                    f"{path.name}: forge hook {hook!r} has unknown backend {backend!r}; "
                    f"known: {sorted(_FORGE_BACKENDS)}"
                )
            for key in ("poll_timeout", "poll_interval"):
                if key not in binding:
                    continue
                if handler != "ci_poll":
                    raise RegistryError(
                        f"{path.name}: forge hook {hook!r} has {key!r}, which applies "
                        "only to a ci_poll handler"
                    )
                value = binding[key]
                # bool is an int in Python, and `poll_timeout: true` is a typo,
                # not a one-second deadline.
                bad = isinstance(value, bool) or not isinstance(value, int | float)
                # .inf is a node that never returns and never frees its intake
                # slot; .nan goes straight into asyncio.sleep.
                bad = bad or not math.isfinite(value)
                # A zero timeout is a meaningful single-shot check. A zero
                # *interval* is a hot loop: it would re-run the forge CLI as
                # fast as a thread can return for the whole timeout. Tests that
                # want no wait pass it to `run_task` directly, not through here.
                if key == "poll_timeout":
                    bad = bad or value < 0
                    wanted = "non-negative number"
                else:
                    bad = bad or value <= 0
                    wanted = "positive number"
                if bad:
                    raise RegistryError(
                        f"{path.name}: forge hook {hook!r} {key!r} must be a "
                        f"{wanted}, not {value!r}"
                    )
        if kind == "subprocess" and not (
            isinstance(binding.get("command"), list)
            and all(isinstance(x, str) for x in binding["command"])
        ):
            raise RegistryError(
                f"{path.name}: subprocess hook {hook!r} needs a list-of-strings 'command'"
            )

        agent_only = (
            "profile",
            "model",
            "escalate_model",
            "deny_tools",
            "steering",
            "skill",
            "artifact",
            "effort",
            "allowed_tools",
            "permission_mode",
        )
        if kind == "agent":
            profiles = _agent_profiles()
            profile = binding.get("profile", "claude")
            if profile not in profiles:
                raise RegistryError(
                    f"{path.name}: hook {hook!r} has unknown profile {profile!r}; "
                    f"known: {sorted(profiles)}"
                )
            for key in ("model", "escalate_model"):
                if binding.get(key) is not None and not isinstance(binding[key], str):
                    raise RegistryError(f"{path.name}: hook {hook!r} {key!r} must be a string")
            if "effort" in binding and binding["effort"] not in _EFFORT_LEVELS:
                raise RegistryError(
                    f"{path.name}: hook {hook!r} 'effort' must be one of "
                    f"{sorted(_EFFORT_LEVELS)}; got {binding['effort']!r}"
                )
            if "permission_mode" in binding and binding["permission_mode"] not in _PERMISSION_MODES:
                raise RegistryError(
                    f"{path.name}: hook {hook!r} 'permission_mode' must be one of "
                    f"{sorted(_PERMISSION_MODES)}; got {binding['permission_mode']!r}"
                )
            for key in ("deny_tools", "steering", "allowed_tools"):
                v = binding.get(key, [])
                if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} {key!r} must be a list of strings"
                    )
            try:
                _steering.validate(steering_dir, binding.get("steering", []), where=path.name)
            except _steering.SteeringError as exc:
                raise RegistryError(str(exc)) from exc
            if "skill" in binding:
                try:
                    _skill.validate(skills_dir, binding["skill"], where=path.name)
                except _skill.SkillError as exc:
                    raise RegistryError(str(exc)) from exc
            if "artifact" in binding:
                art = binding["artifact"]
                if not isinstance(art, str) or not _ARTIFACT_KIND.fullmatch(art):
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} 'artifact' must be a bare lowercase "
                        f"kind like 'spec' or 'plan'; got {art!r}"
                    )
        else:
            for key in agent_only:
                if key in binding:
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} is kind {kind!r}; {key!r} applies "
                        "only to an agent hook"
                    )

        # Last, so the agent-only keys keep their own sharper message above: a
        # key nobody reads is a setting that silently does nothing — a
        # `deny_tool:` typo denies no tool and fails nowhere, the same failure
        # an unknown *profile* is already rejected for.
        if kind == "builtin":
            known = {"kind", "handler"}
        elif kind == "forge":
            known = {"kind", "handler", "backend", "poll_timeout", "poll_interval"}
        else:
            known = {"kind", "command"}
        # `interactive` is UI-facing rather than dispatch-facing (02 §13): the
        # registry round-trips it through Settings today, ahead of the screen
        # that will read it.
        known |= {"interactive"}
        if kind == "agent":
            known |= set(agent_only)
        if "interactive" in binding and not isinstance(binding["interactive"], bool):
            raise RegistryError(f"{path.name}: hook {hook!r} 'interactive' must be a boolean")
        unknown = sorted(set(binding) - known)
        if unknown:
            raise RegistryError(
                f"{path.name}: hook {hook!r} has unknown key(s) {unknown}; "
                f"a {kind} hook takes {sorted(known)}"
            )
    return Registry(hooks=data["hooks"])


def load_templates(dir: str | Path, registry: Registry) -> TemplateSet:
    valid: dict[str, Template] = {}
    invalid: dict[str, str] = {}

    for path in sorted(Path(dir).glob("*.yaml")):
        if path.name in CONFIG_FILES:
            continue
        stem = path.stem
        try:
            data = yaml.safe_load(path.read_text())
        except (OSError, ValueError, yaml.YAMLError) as exc:
            invalid[stem] = f"{path.name}: cannot read/parse: {exc}"
            continue

        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            invalid[stem] = f"{path.name}: missing a string 'id'"
            continue
        tid = data["id"]
        if tid in valid or tid in invalid:
            invalid[stem] = f"{path.name}: duplicate template id {tid!r}"
            continue

        nodes = data.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            invalid[tid] = f"template {tid!r}: 'nodes' must be a non-empty list"
            continue
        if not all(
            isinstance(n, dict)
            and isinstance(n.get("id"), str)
            and isinstance(n.get("tasks"), list)
            and all(isinstance(t, str) for t in n["tasks"])
            for n in nodes
        ):
            invalid[tid] = (
                f"template {tid!r}: each node needs a string 'id' and a list-of-strings 'tasks'"
            )
            continue

        bad_gates = sorted(
            {
                n["gate_after"]
                for n in nodes
                if n.get("gate_after") is not None and n["gate_after"] not in GATE_NAMES
            }
        )
        if bad_gates:
            invalid[tid] = f"template {tid!r}: unknown gate_after value(s) {bad_gates}"
            continue

        bad_loop = next(
            (
                n["id"]
                for n in nodes
                if "fix_loop" in n
                and n["fix_loop"] is not None
                and not (isinstance(n["fix_loop"], str) and n["fix_loop"])
            ),
            None,
        )
        if bad_loop is not None:
            invalid[tid] = (
                f"template {tid!r}: node {bad_loop!r} 'fix_loop' must be a non-empty string or null"
            )
            continue

        empty_loop = next((n["id"] for n in nodes if n.get("fix_loop") and not n["tasks"]), None)
        if empty_loop is not None:
            invalid[tid] = (
                f"template {tid!r}: node {empty_loop!r} has 'fix_loop' but no tasks to measure"
            )
            continue

        # The step between "a task in this node failed" and needs_human
        # (Kraft-rv6i): somewhere to put a blocker that is not the code -- a
        # merge request missing a label its pipeline requires -- and then let
        # the node measure itself again.
        bad_recover = next(
            (
                n["id"]
                for n in nodes
                if "on_failure" in n
                and n["on_failure"] is not None
                and not (
                    isinstance(n["on_failure"], list)
                    and n["on_failure"]
                    and all(isinstance(t, str) for t in n["on_failure"])
                )
            ),
            None,
        )
        if bad_recover is not None:
            invalid[tid] = (
                f"template {tid!r}: node {bad_recover!r} 'on_failure' must be a "
                f"non-empty list of strings or null"
            )
            continue

        # Not both: a fix_loop node already re-measures itself after its fix
        # task runs, so a second remediation would race the first for the same
        # failure and neither would know what the other changed.
        both = next((n["id"] for n in nodes if n.get("on_failure") and n.get("fix_loop")), None)
        if both is not None:
            invalid[tid] = (
                f"template {tid!r}: node {both!r} has both 'fix_loop' and 'on_failure'; "
                f"the fix loop is already that node's remediation"
            )
            continue

        # Where a rejected gate sends the chain (Kraft-ko7j). At or before the
        # declaring node, because a rejection is backward motion: a forward
        # target would let a gate skip the nodes between it and the target
        # without ever running them.
        at = {n["id"]: i for i, n in enumerate(nodes)}
        bad_reject = next(
            (
                n["id"]
                for i, n in enumerate(nodes)
                if n.get("reject_to") is not None
                and (not isinstance(n["reject_to"], str) or at.get(n["reject_to"], len(nodes)) > i)
            ),
            None,
        )
        if bad_reject is not None:
            invalid[tid] = (
                f"template {tid!r}: node {bad_reject!r} 'reject_to' must name a node "
                f"of this template at or before it"
            )
            continue

        unknown = sorted(
            {
                t
                for n in nodes
                for t in list(n["tasks"]) + list(n.get("on_failure") or [])
                if t not in registry.hooks
            }
        )
        if unknown:
            invalid[tid] = f"template {tid!r}: hook(s) {unknown} are not in the registry"
            continue

        valid[tid] = Template(id=tid, nodes=nodes)

    return TemplateSet(valid=valid, invalid=invalid)


def materialize(template: Template, *, satisfied_gates: frozenset[str] = frozenset()) -> dict:
    """The template's nodes, minus any whose gate an intake attachment already
    satisfies — the item genuinely has no spec node, rather than one skipped at
    runtime (Kraft-dgh)."""
    return {
        "template_id": template.id,
        "nodes": [
            {
                "id": n["id"],
                "tasks": list(n["tasks"]),
                "gate_after": n.get("gate_after"),
                "fix_loop": n.get("fix_loop"),
                "on_failure": list(n["on_failure"]) if n.get("on_failure") else None,
                "reject_to": n.get("reject_to"),
            }
            for n in template.nodes
            if n.get("gate_after") not in satisfied_gates
        ],
    }
