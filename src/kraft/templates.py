from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from kraft import sandbox as _sandbox
from kraft import skill as _skill
from kraft import steering as _steering
from kraft.paths import default_skills_dir
from kraft.store import OVERRIDABLE_NODE_FIELDS

if TYPE_CHECKING:
    from kraft import harness

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


def with_steps(node: dict) -> dict:
    """`node` with both `steps` and `tasks` populated, whichever it was given.

    The two keys are one fact in two shapes, on purpose. Ordering has exactly
    one consumer (`executor.dispatch.measure_node`); the flat list has twenty,
    across the backend and the SPA. Emitting both means the ordering consumer
    reads `steps` and nothing else has to learn that groups exist -- a
    `steps`-only node would make `n.tasks ?? []` evaluate to `[]` all over the
    frontend, silently, and nesting inside `tasks` would make `n.tasks.length`
    report a group count as a task count.

    Mutates nothing: returns a new dict, because both callers (`materialize`
    and the chain-review splice) are building fresh node dicts anyway.
    """
    if node.get("steps"):
        groups = [list(g) for g in node["steps"]]
    else:
        groups = [list(node.get("tasks") or [])]
    return {**node, "steps": groups, "tasks": [t for g in groups for t in g]}


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
#: The levels an agent override's `effort` may take when it is validated with
#: no harness in scope (a work item's own override, a node override) --
#: `load_registry`'s own per-binding `effort` check is against the named
#: harness's own `values:` instead (leak 10 in the design spec), since
#: `minimal` is real for codex and invalid for claude. Checked at config load
#: rather than at dispatch: a typo reaching the CLI fails the node *after* the
#: work item has already paid for a worktree and a session (Kraft-tff).
_EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}

#: The keys a `defaults.agent` block may set -- the same closed set an agent
#: hook's own binding is checked against below, hoisted to module scope so
#: this list and the defaults-merge pre-pass share one definition instead of
#: drifting into two. No `_PERMISSION_MODES` beside it any more: the modes a
#: binding may name are the named harness's own `values:` (leak 9 in the
#: design spec), since `yolo` is real for gemini and invalid for claude.
_AGENT_ONLY_KEYS = frozenset(
    {
        "profile",
        "harness",
        "model",
        "escalate_model",
        "deny_tools",
        "steering",
        "skill",
        "artifact",
        "effort",
        "allowed_tools",
        "permission_mode",
    }
)
#: The `_AGENT_ONLY_KEYS` that merge as a list -- default's items first, then
#: the binding's own, deduped -- rather than binding-wins-or-not.
_AGENT_LIST_KEYS = frozenset({"deny_tools", "steering", "allowed_tools"})


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class Registry:
    hooks: dict
    #: `hooks`, before the per-binding defaults (`harness: claude`, etc.) this
    #: loader normalises in -- what the settings API hands back so a GET/PUT
    #: round trip, and a `reload` that keeps the last good config, both echo
    #: exactly what was on disk rather than growing keys nobody wrote. `None`
    #: for a `Registry` built by hand (most tests): dispatch and doctor never
    #: read this field, only the settings routes do.
    raw: dict | None = None


@dataclass(frozen=True)
class Template:
    id: str
    nodes: list


@dataclass(frozen=True)
class TemplateSet:
    valid: dict
    invalid: dict


def _harnesses() -> harness.HarnessSet:
    # Function-local, same reason the old `_agent_profiles` was: a config
    # loader importing the adapter layer at module scope invites a cycle.
    from kraft import harness

    return harness.load(None)


#: Binding keys that name a capability, and so are checked against the
#: harness's own declaration. `escalate_model` is checked as `model`: it is
#: the same capability used on a different turn.
_CAPABILITY_KEYS = {
    "model": "model",
    "escalate_model": "model",
    "effort": "effort",
    "deny_tools": "deny_tools",
    "allowed_tools": "allowed_tools",
    "permission_mode": "permission_mode",
}


def _merge_agent_defaults(data: dict, path: Path) -> None:
    """Merge a registry's `defaults.agent` block into every `kind: agent`
    binding under `hooks`, in place, before `load_registry`'s own per-binding
    validation runs below -- so a typo in a default fails at load, the same
    as a typo in the binding itself already does. Scalars: the binding's own
    value wins over the default. Lists: the default's items first, then the
    binding's own, deduped.
    """
    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict) or (set(defaults) - {"agent"}):
        raise RegistryError(f"{path.name}: 'defaults' takes only an 'agent' key")
    agent_defaults = defaults.get("agent") or {}
    if not isinstance(agent_defaults, dict):
        raise RegistryError(f"{path.name}: 'defaults.agent' must be a mapping")
    unknown = sorted(set(agent_defaults) - _AGENT_ONLY_KEYS)
    if unknown:
        raise RegistryError(f"{path.name}: 'defaults.agent' has unknown key(s) {unknown}")
    for key in _AGENT_LIST_KEYS:
        if key in agent_defaults and not (
            isinstance(agent_defaults[key], list)
            and all(isinstance(x, str) for x in agent_defaults[key])
        ):
            raise RegistryError(f"{path.name}: 'defaults.agent' {key!r} must be a list of strings")
    if not agent_defaults:
        return
    for hook, binding in data["hooks"].items():
        if not isinstance(binding, dict) or binding.get("kind") != "agent":
            continue
        for key, dval in agent_defaults.items():
            if key in _AGENT_LIST_KEYS:
                if key in binding and not (
                    isinstance(binding[key], list) and all(isinstance(x, str) for x in binding[key])
                ):
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} {key!r} must be a list of strings"
                    )
                own = binding.get(key, [])
                merged = list(dval)
                for x in own:
                    if x not in merged:
                        merged.append(x)
                binding[key] = merged
            elif key not in binding:
                binding[key] = dval


def load_registry(
    path: str | Path,
    *,
    steering_dir: Path | None = None,
    skills_dir: Path | None = None,
    harnesses: harness.HarnessSet | None = None,
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
    # Snapshot before anything below normalises defaults into each binding in
    # place -- `defaults.agent` here, `harness: claude` in the loop. This is
    # what `raw` hands back untouched, and the registry PUT carries the
    # `defaults` block over from disk itself, so the round trip stays exact.
    raw = copy.deepcopy(data["hooks"])
    _merge_agent_defaults(data, path)
    for hook, binding in data["hooks"].items():
        if not isinstance(binding, dict) or "kind" not in binding:
            raise RegistryError(f"{path.name}: hook {hook!r} is missing 'kind'")
        kind = binding["kind"]
        if kind not in _VALID_KINDS:
            raise RegistryError(f"{path.name}: hook {hook!r} has unknown kind {kind!r}")
        if kind == "builtin" and not isinstance(binding.get("handler"), str):
            raise RegistryError(f"{path.name}: builtin hook {hook!r} needs a string 'handler'")
        if kind == "agent" and "command" in binding and not isinstance(binding["command"], str):
            raise RegistryError(
                f"{path.name}: agent hook {hook!r} 'command' must be a string "
                "(it overrides argv[0] only)"
            )
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

        if kind == "agent":
            hs = harnesses if harnesses is not None else _harnesses()
            # `profile:` is the pre-harness spelling. Kept for one release so
            # an install that edited registry.yaml still loads; `harness:`
            # wins when both are present.
            hid = binding.get("harness") or binding.get("profile") or "claude"
            if hid in hs.invalid:
                raise RegistryError(
                    f"{path.name}: hook {hook!r} names harness {hid!r}, which failed "
                    f"to load: {hs.invalid[hid]}"
                )
            if hid not in hs.valid:
                raise RegistryError(
                    f"{path.name}: hook {hook!r} has unknown harness {hid!r}; "
                    f"known: {sorted(hs.valid)}"
                )
            h = hs.valid[hid]
            # Normalised here so no consumer re-derives the default.
            binding["harness"] = hid
            for key in ("model", "escalate_model"):
                if binding.get(key) is not None and not isinstance(binding[key], str):
                    raise RegistryError(f"{path.name}: hook {hook!r} {key!r} must be a string")
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
            # Fail at load, no emulation: a binding naming a capability its
            # harness does not declare, or a value outside that capability's
            # own `values:`, is rejected here rather than at 3am.
            for key, capability in _CAPABILITY_KEYS.items():
                if key not in binding or binding[key] is None:
                    continue
                if not h.supports(capability):
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} sets {key!r}, but harness "
                        f"{hid!r} ({h.path}) declares no {capability!r} capability"
                    )
                given = binding[key]
                values = given if isinstance(given, list) else [given]
                for one in values:
                    if not isinstance(one, str):
                        raise RegistryError(
                            f"{path.name}: hook {hook!r} {key!r} must be a string "
                            "or list of strings"
                        )
                    if not h.value_ok(capability, one):
                        raise RegistryError(
                            f"{path.name}: hook {hook!r} {key!r}={one!r} is not accepted "
                            f"by harness {hid!r} ({h.path})"
                        )
        else:
            for key in _AGENT_ONLY_KEYS:
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
        #
        # `on_failure` is kind-agnostic on purpose (spec §4): the motivating
        # repair hangs off `on.ci.poll`, which is `kind: forge`. Deliberately
        # NOT in `_AGENT_ONLY_KEYS`, and therefore not settable through
        # `defaults.agent` either -- a repair silently inherited by every
        # agent binding is the opposite of what a per-task repair is for.
        known |= {"interactive", "timeout", "repos", "sandbox", "on_failure"}
        if kind == "agent":
            known |= _AGENT_ONLY_KEYS
        if "interactive" in binding and not isinstance(binding["interactive"], bool):
            raise RegistryError(f"{path.name}: hook {hook!r} 'interactive' must be a boolean")
        unknown = sorted(set(binding) - known)
        if unknown:
            raise RegistryError(
                f"{path.name}: hook {hook!r} has unknown key(s) {unknown}; "
                f"a {kind} hook takes {sorted(known)}"
            )
    # Binding-level `on_failure` (spec §4): a repair that travels with the task
    # rather than with whichever node happens to run it. Validated here, after
    # the per-binding loop rather than inside it, because a repair hook is
    # itself a hook in this same file -- the names it points at are only all
    # known once every binding has been read.
    for hook, binding in data["hooks"].items():
        repair = binding.get("on_failure")
        if repair is None:
            continue
        if (
            not isinstance(repair, list)
            or not repair
            or not all(isinstance(t, str) for t in repair)
        ):
            raise RegistryError(
                f"{path.name}: hook {hook!r} 'on_failure' must be a non-empty list of strings"
            )
        # A hook naming itself would dispatch its own repair, fail again, and
        # do it forever. The general cycle (a -> b -> a) is not checked: a
        # repair's own `on_failure` is never dispatched (`dispatch.measure_node`
        # runs one repair layer only), so self-reference is the only shape that
        # can actually loop.
        if hook in repair:
            raise RegistryError(
                f"{path.name}: hook {hook!r} names itself in 'on_failure'; "
                "a hook cannot be its own repair"
            )
        missing = sorted(set(repair) - set(data["hooks"]))
        if missing:
            raise RegistryError(
                f"{path.name}: hook {hook!r} 'on_failure' names unknown hook(s) {missing}"
            )

    return Registry(hooks=data["hooks"], raw=raw)


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
        and (
            (isinstance(n.get("tasks"), list) and all(isinstance(t, str) for t in n["tasks"]))
            or n.get("steps") is not None
        )
        for n in nodes
    ):
        return ["each node needs a string 'id' and a list-of-strings 'tasks' (or 'steps')"]

    both = next((n["id"] for n in nodes if n.get("steps") and n.get("tasks")), None)
    if both is not None:
        return [
            f"node {both!r} declares both 'steps' and 'tasks'; a node has one or "
            "the other -- 'tasks' is the one-group shorthand"
        ]
    bad_steps = next(
        (
            n["id"]
            for n in nodes
            if "steps" in n
            and n["steps"] is not None
            and (
                not isinstance(n["steps"], list)
                or not n["steps"]
                or not all(
                    isinstance(g, list) and g and all(isinstance(t, str) for t in g)
                    for g in n["steps"]
                )
            )
        ),
        None,
    )
    if bad_steps is not None:
        return [
            f"node {bad_steps!r} 'steps' must be a non-empty list of non-empty lists of strings"
        ]

    # One code path sees one shape from here on: a `steps` node's hooks reach
    # the unknown-hook scan below via its normalized `tasks`, same as a plain
    # `tasks` node's.
    nodes = [with_steps(n) for n in nodes]

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


#: The keys `_resolve_template_dict` treats as composition -- meaningless,
#: and rejected, on a template that does not `extends` (spec §2's "Hard load
#: errors" posture: a key that silently does nothing is worse than a
#: rejected one).
_COMPOSITION_KEYS = ("remove", "insert_before", "insert_after")


def _resolve_template_dict(
    tid: str, raw_by_id: dict[str, dict], *, visiting: frozenset[str] = frozenset()
) -> list:
    """Resolve one raw template dict into a flat node list, recursively
    resolving `extends` first. Raises `RegistryError` -- caught by
    `load_templates`'s own per-template try/except below, the same as every
    other load-time defect -- since this runs before any node-level
    validation has produced a node to attach an error to.

    Every node returned is a fresh `dict` copy: two templates extending the
    same base, or one template's own `insert_before`/`insert_after`, must
    never share a node dict a sibling resolution could then mutate.
    """
    data = raw_by_id[tid]
    extends = data.get("extends")

    if extends is None:
        nodes = data.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise RegistryError(f"template {tid!r}: 'nodes' must be a non-empty list")
        used = sorted(k for k in _COMPOSITION_KEYS if k in data)
        if used:
            raise RegistryError(f"template {tid!r}: {used} require 'extends'")
        bad = next((n for n in nodes if not isinstance(n, dict)), None)
        if bad is not None:
            raise RegistryError(f"template {tid!r}: node entries must be mappings, got {bad!r}")
        return [dict(n) for n in nodes]

    if "nodes" in data:
        raise RegistryError(f"template {tid!r}: cannot set both 'extends' and 'nodes'")
    if not isinstance(extends, str):
        raise RegistryError(f"template {tid!r}: 'extends' must be a string template id")
    if extends not in raw_by_id:
        raise RegistryError(f"template {tid!r}: extends unknown template {extends!r}")
    if extends in visiting:
        raise RegistryError(f"template {tid!r}: 'extends' cycle at {extends!r}")

    nodes = _resolve_template_dict(extends, raw_by_id, visiting=visiting | {tid})

    remove = data.get("remove", [])
    if not isinstance(remove, list) or not all(isinstance(x, str) for x in remove):
        raise RegistryError(f"template {tid!r}: 'remove' must be a list of node ids")
    base_ids = {n["id"] for n in nodes if isinstance(n.get("id"), str)}
    unknown_remove = sorted(set(remove) - base_ids)
    if unknown_remove:
        raise RegistryError(f"template {tid!r}: 'remove' names unknown node id(s) {unknown_remove}")
    nodes = [n for n in nodes if n.get("id") not in remove]

    for key in ("insert_before", "insert_after"):
        spec = data.get(key, {})
        if not isinstance(spec, dict):
            raise RegistryError(
                f"template {tid!r}: {key!r} must be a mapping of anchor to node list"
            )
        for anchor, extra in spec.items():
            if not isinstance(extra, list) or not all(isinstance(n, dict) for n in extra):
                raise RegistryError(
                    f"template {tid!r}: {key!r}[{anchor!r}] must be a list of node dicts"
                )
            ids_now = [n.get("id") for n in nodes]
            if anchor not in ids_now:
                raise RegistryError(
                    f"template {tid!r}: {key!r} names unknown anchor node id {anchor!r}"
                )
            idx = ids_now.index(anchor)
            offset = idx if key == "insert_before" else idx + 1
            nodes[offset:offset] = [dict(n) for n in extra]

    ids = [n.get("id") for n in nodes]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise RegistryError(f"template {tid!r}: duplicate node id(s) {dupes}")

    return nodes


def load_templates(dir: str | Path, registry: Registry) -> TemplateSet:
    valid: dict[str, Template] = {}
    invalid: dict[str, str] = {}
    raw_by_id: dict[str, dict] = {}

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
        if tid in raw_by_id or tid in invalid:
            invalid[stem] = f"{path.name}: duplicate template id {tid!r}"
            continue
        raw_by_id[tid] = data

    for tid in raw_by_id:
        try:
            nodes = _resolve_template_dict(tid, raw_by_id)
        except RegistryError as exc:
            invalid[tid] = str(exc)
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
            with_steps(
                {
                    "id": n["id"],
                    "tasks": list(n.get("tasks") or []),
                    "steps": [list(g) for g in n["steps"]] if n.get("steps") else None,
                    "gate_after": n.get("gate_after"),
                    "fix_loop": n.get("fix_loop"),
                    "on_failure": list(n["on_failure"]) if n.get("on_failure") else None,
                    "reject_to": n.get("reject_to"),
                    "rebase_bounce_to": n.get("rebase_bounce_to"),
                    "auto_escalate": n.get("auto_escalate"),
                    "auto_escalate_stuck": n.get("auto_escalate_stuck"),
                    "auto_escalate_delay_s": n.get("auto_escalate_delay_s"),
                }
            )
            for n in template.nodes
            if n.get("gate_after") not in satisfied_gates and n["id"] not in skip_nodes
        ],
    }
