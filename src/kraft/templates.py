from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from kraft import sandbox as _sandbox
from kraft import skill as _skill
from kraft import steering as _steering
from kraft.paths import default_skills_dir
from kraft.store import OVERRIDABLE_NODE_FIELDS

_VALID_KINDS = {"builtin", "agent", "subprocess", "forge"}
_FORGE_HANDLERS = {"open_mr", "sync_mr", "ci_poll", "merge", "merge_watch"}
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
        "theme.yaml",
    }
)
# The complete gate set. Public because the API validates approve/reject against it
# and the chain-review skill documents it — a second copy is how those drift apart.
GATE_NAMES = {"spec_approval", "plan_approval", "chain_finalized", "human_review_approval"}
#: Node keys `materialize()` sets from a template but the chain-review skill's
#: prompt never teaches an agent to reproduce (Kraft-eod0) -- its schema is
#: `{id, tasks, gate_after, fix_loop}`, four of the eight keys a real node
#: carries. An agent emitting an "unchanged" node only knows those four, so
#: the splice that replaces the tail wholesale (`kraft.api.routes.gates._splice_chain_review`)
#: must carry these forward from the node they replace rather than trust an
#: agent-authored dict to know they exist.
NODE_CARRYOVER_FIELDS = (
    "on_failure",
    "reject_to",
    "rebase_bounce_to",
    "auto_escalate",
    "auto_escalate_stuck",
    "auto_escalate_delay_s",
)


#: The `NODE_CARRYOVER_FIELDS` a chain-review node dict is never allowed to
#: set for itself -- `on_failure`/`reject_to`/`rebase_bounce_to` are the
#: reviewer's to propose (point 1), but `auto_escalate`/`auto_escalate_stuck`/
#: `auto_escalate_delay_s` are the human PATCH route's alone (SKILL.md: "still
#: never yours to set"). `strip_non_proposable_carryover_fields` below drops
#: these off a reviewer node so they can only ever reach `chain_definition`
#: through the carry-forward path, never an agent-authored value.
NON_PROPOSABLE_CARRYOVER_FIELDS = ("auto_escalate", "auto_escalate_stuck", "auto_escalate_delay_s")


def strip_non_proposable_carryover_fields(nodes: list) -> list:
    """Drop `NON_PROPOSABLE_CARRYOVER_FIELDS` off every node in `nodes`, in
    place, before `carry_forward_node_fields` runs. Without this, a reviewer
    node that sets `auto_escalate` (etc.) directly splices that value straight
    into `chain_definition` -- `carry_forward_node_fields` only fills fields
    the node *omits*, so a value the node explicitly set survives untouched
    (`kraft.api.routes.gates._splice_chain_review`, Kraft-df4tc)."""
    for n in nodes:
        for field in NON_PROPOSABLE_CARRYOVER_FIELDS:
            n.pop(field, None)
    return nodes


def carry_forward_node_fields(old_nodes: list, new_nodes: list) -> list:
    """Fill `NODE_CARRYOVER_FIELDS` on `new_nodes` from the old node sharing its
    `id`, for whichever fields the new node did not itself set. A node id with
    no old counterpart (one the reviewer added) is left alone -- the reviewer
    cannot invent a repair task or a reject target the skill never taught it to
    name, so a genuinely new node gets `None` for all of these, same as
    `materialize()` gives any node lacking them."""
    old_by_id = {
        n["id"]: n for n in old_nodes if isinstance(n, dict) and isinstance(n.get("id"), str)
    }
    for n in new_nodes:
        old = old_by_id.get(n.get("id"))
        if old is None:
            continue
        for field in NODE_CARRYOVER_FIELDS:
            if field not in n:
                n[field] = old.get(field)
    return new_nodes


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

        if "timeout" in binding:
            if kind == "builtin":
                raise RegistryError(
                    f"{path.name}: hook {hook!r} is kind 'builtin'; 'timeout' applies only to "
                    "a subprocess, agent, or forge hook"
                )
            t = binding["timeout"]
            if isinstance(t, bool) or not isinstance(t, int | float) or t <= 0:
                raise RegistryError(
                    f"{path.name}: hook {hook!r} 'timeout' must be a positive number of minutes"
                )
        if "repos" in binding:
            repos_raw = binding["repos"]
            if not isinstance(repos_raw, dict):
                raise RegistryError(
                    f"{path.name}: hook {hook!r} 'repos' must be a mapping of repo path to override"
                )
            for repo_path, override in repos_raw.items():
                if not isinstance(override, dict) or "enabled" not in override:
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} 'repos'[{repo_path!r}] "
                        "needs at least 'enabled'"
                    )
                if not isinstance(override["enabled"], bool):
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} 'repos'[{repo_path!r}] "
                        "'enabled' must be a boolean"
                    )
                cmd = override.get("command")
                if cmd is not None and not (
                    isinstance(cmd, str)
                    or (isinstance(cmd, list) and all(isinstance(x, str) for x in cmd))
                ):
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} 'repos'[{repo_path!r}] 'command' must be "
                        "a string or list of strings"
                    )

        if "sandbox" in binding:
            if kind not in ("agent", "subprocess"):
                raise RegistryError(
                    f"{path.name}: hook {hook!r} is kind {kind!r}; 'sandbox' applies only to "
                    "a subprocess or agent hook"
                )
            try:
                _sandbox.validate(binding["sandbox"], where=f"{path.name}: hook {hook!r}")
            except _sandbox.SandboxError as exc:
                raise RegistryError(str(exc)) from exc

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
        # `interactive`/`timeout`/`repos` are UI-facing rather than
        # dispatch-facing (02 §13): the registry round-trips them through
        # Settings today, ahead of the screen/executor that will read them.
        # `timeout` is rejected for `builtin` above, so this stays a plain
        # superset with no behaviour change for `builtin`.
        known |= {"interactive", "timeout", "repos", "sandbox"}
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


def validate_nodes(
    nodes: list, registry: Registry, *, preceding_ids: frozenset[str] = frozenset()
) -> list[str]:
    """The per-node rules a chain's node list is held to: shape, hook set
    membership, `GATE_NAMES` membership, and a `fix_loop` node needing at
    least one task. Takes a bare node list and a registry rather than a whole
    `Template` so the chain-review splice path (Kraft-hm0) can run the same
    checks over an agent's revised tail before it reaches `chain_definition`
    -- one rule set, two callers, not a second, drifting copy of it
    (Kraft-unk). `load_templates` below calls it once per template at load
    time; `kraft.api.routes.gates._splice_chain_review` calls it once per
    `revised_chain_nodes` payload at gate-approval time -- that caller only
    ever hands it the not-yet-run tail, so `preceding_ids` names the ids of
    already-run nodes (outside `nodes`) that still count as "at or before"
    for `reject_to`/`rebase_bounce_to` (Kraft-df4tc).

    Returns error strings, empty if valid. Stops at the first failing rule
    category rather than collecting every node's every problem -- the same
    one-message-at-a-time shape `load_templates` produced before this was
    factored out of it.
    """
    if not isinstance(nodes, list) or not all(
        isinstance(n, dict)
        and isinstance(n.get("id"), str)
        and isinstance(n.get("tasks"), list)
        and all(isinstance(t, str) for t in n["tasks"])
        for n in nodes
    ):
        return ["each node needs a string 'id' and a list-of-strings 'tasks'"]

    bad_gates = sorted(
        {
            n["gate_after"]
            for n in nodes
            if n.get("gate_after") is not None and n["gate_after"] not in GATE_NAMES
        }
    )
    if bad_gates:
        return [f"unknown gate_after value(s) {bad_gates}"]

    empty_loop = next((n["id"] for n in nodes if n.get("fix_loop") and not n["tasks"]), None)
    if empty_loop is not None:
        return [f"node {empty_loop!r} has 'fix_loop' but no tasks to measure"]

    # on_failure (Kraft-df4tc point 1) is reviewer-settable but only ever
    # meant as a non-empty list of hook names -- a bare string ("a.b.c")
    # is iterable too and would otherwise sail through the unknown-hook scan
    # below (which only looks inside a list) and reach walk.py's
    # `list(node["on_failure"])`, which yields the string's characters as
    # hook names.
    bad_on_failure = next(
        (
            n["id"]
            for n in nodes
            if "on_failure" in n
            and n["on_failure"] is not None
            and (
                not isinstance(n["on_failure"], list)
                or not n["on_failure"]
                or not all(isinstance(t, str) for t in n["on_failure"])
            )
        ),
        None,
    )
    if bad_on_failure is not None:
        return [f"node {bad_on_failure!r} 'on_failure' must be a non-empty list of strings"]

    unknown = sorted(
        {
            t
            for n in nodes
            # A malformed `on_failure` already failed the shape check above,
            # so this `isinstance` guard only matters for `load_templates`'s
            # own not-yet-validated call: without it, a bare string would be
            # misread as a list of one-character "hooks".
            for t in [
                *n["tasks"],
                *(n["on_failure"] if isinstance(n.get("on_failure"), list) else []),
            ]
            if t not in registry.hooks
        }
    )
    if unknown:
        return [f"hook(s) {unknown} are not in the registry"]

    at = {n["id"]: i for i, n in enumerate(nodes)}
    for field in ("reject_to", "rebase_bounce_to"):
        bad = next(
            (
                n["id"]
                for i, n in enumerate(nodes)
                if n.get(field) is not None
                and (
                    not isinstance(n[field], str)
                    or (n[field] not in preceding_ids and at.get(n[field], len(nodes)) > i)
                )
            ),
            None,
        )
        if bad is not None:
            return [f"node {bad!r} {field!r} must name a node at or before it"]

    return []


def validate_model_effort_fields(overrides: dict) -> list[str]:
    """The type/membership rules `model`, `escalate_model`, and `effort` are
    held to wherever one appears as an override -- a work item's own
    `agent_overrides` (`validate_agent_overrides` below) and a per-node
    override (`kraft.api.routes.work_items._validate_node_overrides`,
    Kraft-df4tc) and a chain-review-proposed `proposed_node_overrides`
    (`kraft.api.routes.gates._splice_chain_review`, same bead). One field-
    level check, three callers, so none of them can drift into a different
    idea of a valid `effort` (Kraft-unk's reasoning, at the field level
    rather than the whole-object level `validate_agent_overrides` already
    applies it at).

    Returns error strings with no `agent_overrides`/node-id prefix -- each
    caller's own message already carries the context a bare "'effort' must
    be one of..." needs.
    """
    for key in ("model", "escalate_model"):
        if key in overrides and overrides[key] is not None and not isinstance(overrides[key], str):
            return [f"{key!r} must be a string or null"]
    if "effort" in overrides and overrides["effort"] not in _EFFORT_LEVELS:
        return [f"'effort' must be one of {sorted(_EFFORT_LEVELS)}; got {overrides['effort']!r}"]
    return []


#: The only fields a chain-review artifact's `proposed_node_overrides` may
#: set (Kraft-df4tc point 5) -- a strict subset of `OVERRIDABLE_NODE_FIELDS`.
#: A human can dial `auto_escalate`/`attempts`/`wall_clock_s` through the
#: PATCH route; an agent's chain-review proposal never can, no matter what it
#: invents -- the chain-review skill text promises exactly that.
PROPOSABLE_NODE_OVERRIDE_FIELDS = frozenset({"model", "escalate_model", "effort"})


def validate_proposed_node_overrides(proposed: dict) -> list[str]:
    """`proposed_node_overrides` on one node of a chain-review artifact
    (`kraft.api.routes.gates._splice_chain_review`): keys restricted to
    `PROPOSABLE_NODE_OVERRIDE_FIELDS`, on top of `validate_model_effort_fields`'s
    type checks. Anything else -- `auto_escalate` included -- is never the
    reviewer's to set (Kraft-df4tc).
    """
    extra = set(proposed) - PROPOSABLE_NODE_OVERRIDE_FIELDS
    if extra:
        return [f"cannot propose {sorted(extra)}"]
    return validate_model_effort_fields(proposed)


def validate_node_override_fields(fields: dict) -> list[str]:
    """Everything a `node_overrides`/`proposed_node_overrides` patch for one
    node must hold to: keys inside `kraft.store.OVERRIDABLE_NODE_FIELDS` only,
    each with the right shape. One rule set for the three callers that accept
    such a patch from outside the chain-definition schema --
    `kraft.api.routes.work_items.create_work_item` (POST),
    `_validate_node_overrides` (PATCH), and
    `kraft.api.routes.gates._splice_chain_review` (a chain-review artifact) --
    so a field none of them meant to allow (`auto_escalate`, `attempts`, ...)
    can't reach `store.set_node_overrides` through whichever caller forgets to
    check it (Kraft-df4tc).

    Returns error strings with no node-id prefix -- each caller's own message
    already carries that context.
    """
    extra = set(fields) - OVERRIDABLE_NODE_FIELDS
    if extra:
        return [f"cannot override {sorted(extra)}"]
    if "auto_escalate" in fields and not isinstance(fields["auto_escalate"], bool):
        return ["auto_escalate must be a boolean"]
    if "auto_escalate_stuck" in fields and not isinstance(fields["auto_escalate_stuck"], bool):
        return ["auto_escalate_stuck must be a boolean"]
    if "auto_escalate_delay_s" in fields and (
        not isinstance(fields["auto_escalate_delay_s"], int)
        or isinstance(fields["auto_escalate_delay_s"], bool)
        or fields["auto_escalate_delay_s"] < 0
    ):
        return ["auto_escalate_delay_s must be a non-negative int"]
    if "attempts" in fields and (
        not isinstance(fields["attempts"], int)
        or isinstance(fields["attempts"], bool)
        or fields["attempts"] < 1
    ):
        return ["attempts must be a positive int"]
    if "wall_clock_s" in fields and (
        not isinstance(fields["wall_clock_s"], int)
        or isinstance(fields["wall_clock_s"], bool)
        or fields["wall_clock_s"] < 1
    ):
        return ["wall_clock_s must be a positive int"]
    model_effort = {k: v for k, v in fields.items() if k in ("model", "escalate_model", "effort")}
    if model_effort:
        errs = validate_model_effort_fields(model_effort)
        if errs:
            return errs
    return []


def validate_agent_overrides(overrides: dict) -> list[str]:
    """The rules a work item's own model/effort override (Kraft-4k6l) is held
    to -- exactly what `load_registry` above already checks for an agent
    hook's own `model`/`escalate_model`/`effort`: keys are a subset of
    `{"model", "escalate_model", "effort"}`; `model` and `escalate_model` are
    strings or `None`; `effort` is one of `_EFFORT_LEVELS`. One rule set, one
    exported function -- `kraft.api.routes.work_items` calls this rather than reaching for the
    private `_EFFORT_LEVELS` itself, the same "validate in one place"
    reasoning `validate_nodes` gives for its own two callers, so a work item's
    override and a template's binding cannot drift into two different ideas
    of a valid `effort` (Kraft-unk).

    Returns error strings, empty if valid.
    """
    if not isinstance(overrides, dict):
        return ["agent_overrides must be an object"]
    unknown = sorted(set(overrides) - {"model", "escalate_model", "effort"})
    if unknown:
        return [
            f"agent_overrides has unknown key(s) {unknown}; only model, "
            "escalate_model, effort are allowed"
        ]
    return [f"agent_overrides {e}" for e in validate_model_effort_fields(overrides)]


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

        node_errors = validate_nodes(nodes, registry)
        if node_errors:
            invalid[tid] = f"template {tid!r}: {node_errors[0]}"
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

        # Which gates an agent may review before a human sees them (Kraft-zr3s).
        # Only meaningful beside a gate: on a gateless node it would review
        # nothing, and a config key that silently does nothing is worse than a
        # rejected one.
        bad_auto = next(
            (
                n["id"]
                for n in nodes
                if n.get("auto_escalate") is not None
                and (not isinstance(n["auto_escalate"], bool) or not n.get("gate_after"))
            ),
            None,
        )
        if bad_auto is not None:
            invalid[tid] = (
                f"template {tid!r}: node {bad_auto!r} 'auto_escalate' must be a bool "
                f"on a node that declares a 'gate_after'"
            )
            continue

        # Unlike auto_escalate, meaningful on every node -- a needs_human stop
        # has nothing to do with which node it happened on being a gate.
        bad_aes = next(
            (
                n["id"]
                for n in nodes
                if n.get("auto_escalate_stuck") is not None
                and not isinstance(n["auto_escalate_stuck"], bool)
            ),
            None,
        )
        if bad_aes is not None:
            invalid[tid] = (
                f"template {tid!r}: node {bad_aes!r} 'auto_escalate_stuck' must be a bool"
            )
            continue

        # Same "meaningful everywhere" posture as auto_escalate_stuck: a
        # delay is a wait before whichever mechanism this node's gate or stop
        # already uses, not itself gate-specific.
        bad_delay = next(
            (
                n["id"]
                for n in nodes
                if n.get("auto_escalate_delay_s") is not None
                and (
                    not isinstance(n["auto_escalate_delay_s"], int)
                    or isinstance(n["auto_escalate_delay_s"], bool)
                    or n["auto_escalate_delay_s"] < 0
                )
            ),
            None,
        )
        if bad_delay is not None:
            invalid[tid] = (
                f"template {tid!r}: node {bad_delay!r} 'auto_escalate_delay_s' "
                f"must be a non-negative int"
            )
            continue

        valid[tid] = Template(id=tid, nodes=nodes)

    return TemplateSet(valid=valid, invalid=invalid)


def materialize(
    template: Template,
    *,
    satisfied_gates: frozenset[str] = frozenset(),
    skip_nodes: frozenset[str] = frozenset(),
) -> dict:
    """The template's nodes, minus any whose gate an intake attachment already
    satisfies — the item genuinely has no spec node, rather than one skipped at
    runtime (Kraft-dgh) — and minus any in `skip_nodes` (UI v2 · 04 point 6):
    the intake-time click-to-skip, same "genuinely not in the chain" posture,
    including a gated node (the design strikes `spec` through in 10/m09 even
    though it gates) -- a human choosing this at intake is the same trust an
    attachment trim already gets, so a gate skipped this way is not a gate
    bypassed at runtime, it never existed for this item."""
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
                "rebase_bounce_to": n.get("rebase_bounce_to"),
                "auto_escalate": n.get("auto_escalate"),
                "auto_escalate_stuck": n.get("auto_escalate_stuck"),
                "auto_escalate_delay_s": n.get("auto_escalate_delay_s"),
            }
            for n in template.nodes
            if n.get("gate_after") not in satisfied_gates and n["id"] not in skip_nodes
        ],
    }
