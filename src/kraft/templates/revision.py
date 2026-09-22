"""Plan-driven chain revision (Kraft-oydes, Ruling 208).

A `revise_chain` agent reads the approved spec and plan and writes a
`chain_revision` artifact: a *change set* against the nodes that have not run
yet, never a whole new tail. The pre-V1 splice replaced the tail wholesale and
lost what the reviewer did not re-emit (`on_failure`, gates: Kraft-eod0,
Kraft-gnn1); a change set cannot drop what it does not name.

* `ChangeSet` -- the one strict shape the artifact's body holds (`parse`).
* `revise` -- the change set applied to a materialized chain, validated by the
  same model intake uses; an invalid one raises and never reaches the chain.
* `diff` -- what the revision changed, node by node, for the gate and the
  `chain_revised` event.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from difflib import SequenceMatcher
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from kraft.policy import FROZEN, PolicyError
from kraft.templates.environment import Identifier
from kraft.templates.library import TemplateLibrary, TemplateLibraryError
from kraft.templates.models import (
    PATH_SEPARATOR,
    Chain,
    ExecNode,
    ForgeTask,
    GateNode,
    MaterializedChain,
    ResolvedChain,
    ResolvedNode,
    first_error,
)
from kraft.templates.retry import RetryOverrideError, validate_retry_override

#: The artifact kind a `revise_chain` task produces and a revision gate shows.
CHAIN_REVISION = "chain_revision"

_STRICT = ConfigDict(strict=True, extra="forbid")


class RevisionError(ValueError):
    """A chain revision that cannot be read, or cannot be applied: the reason is
    the message, written for the person at the gate."""


class _Change(BaseModel):
    model_config = _STRICT

    #: The line of the spec or plan that asks for this change. Required: a
    #: change with no stated evidence makes the gate decorative.
    evidence: StrictStr = Field(min_length=1)


class AddedTask(BaseModel):
    """A task of an added node: a library task by name, and nothing else, so a
    revision can only run work an administrator already wrote."""

    model_config = _STRICT

    id: Identifier
    extends: Identifier


class AddedStep(BaseModel):
    model_config = _STRICT

    id: Identifier
    tasks: list[AddedTask] = Field(min_length=1)


class AddedNode(BaseModel):
    """An execution node built from library components only: a library node
    (`extends`), or `tasks`/`steps` of library tasks."""

    model_config = _STRICT

    id: Identifier
    extends: Identifier | None = None
    tasks: list[AddedTask] | None = None
    steps: list[AddedStep] | None = None

    @model_validator(mode="after")
    def _one_shape(self) -> AddedNode:
        shapes = [n for n in ("extends", "tasks", "steps") if getattr(self, n) is not None]
        if len(shapes) != 1:
            raise ValueError(
                f"an added node declares exactly one of extends, tasks or steps, not {shapes}"
            )
        return self

    def authored(self) -> dict:
        """The node as a chain file would write it. Always `kind: exec`: a
        library gate extended here is refused as a kind change, so a revision
        never adds a gate either."""
        return {**self.model_dump(exclude_none=True), "kind": "exec"}


class Skip(_Change):
    node: Identifier


class Add(_Change):
    #: The node the added one runs right after: the revision's own gate, or a
    #: node after it.
    after: Identifier
    node: AddedNode


class Override(_Change):
    """What a revision may set at one canonical path: an agent task's `model`
    and `effort`, and the operational policy values -- bounded, when applied,
    by the administrator maxima and every enclosing scope's cap, exactly as a
    retry's override is. No safety field (tools, sandbox) and no harness: a
    revision decides how much effort the work gets, not what it may touch."""

    model: StrictStr | None = None
    effort: StrictStr | None = None
    timeout_minutes: StrictInt | None = None
    max_attempts: StrictInt | None = None
    time_cap_minutes: StrictInt | None = None
    total_time_cap_minutes: StrictInt | None = None
    token_budget: StrictInt | None = None
    budget_usd: StrictFloat | StrictInt | None = None

    @model_validator(mode="after")
    def _sets_something(self) -> Override:
        if not self.task_config() and not self.policy():
            raise ValueError("an override that sets nothing")
        return self

    def task_config(self) -> dict:
        return {k: v for k in ("model", "effort") if (v := getattr(self, k)) is not None}

    def policy(self) -> dict:
        fields = self.model_dump(exclude_none=True)
        return {k: v for k, v in fields.items() if k not in ("model", "effort", "evidence")}


class ChangeSet(BaseModel):
    """What a `chain_revision` artifact proposes. Empty -- a rationale and no
    change -- is the common, correct answer, and advances without a human."""

    model_config = _STRICT

    rationale: StrictStr = Field(min_length=1)
    skip: list[Skip] = Field(default_factory=list)
    add: list[Add] = Field(default_factory=list)
    #: Keyed by canonical path (`node` or `node.step.task`).
    overrides: dict[str, Override] = Field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.skip or self.add or self.overrides)


#: The whole body after the front matter: one ```json fence, only whitespace
#: around it. Anything the human should read goes inside, in `rationale` and
#: each change's `evidence`.
_BODY = re.compile(r"\A\s*```json\n(.*)\n```\s*\Z", re.DOTALL)


def _refuse_duplicates(pairs: list[tuple[str, object]]) -> dict:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        twice = next(k for k in keys if keys.count(k) > 1)
        raise ValueError(f"key {twice!r} appears twice")
    return dict(pairs)


def parse(text: str) -> ChangeSet:
    """The change set in a `chain_revision` artifact, read strictly: the whole
    body is one fenced JSON object and nothing else, every key is known, and
    no key appears twice. Raises `RevisionError` naming what is wrong."""
    from kraft.index.ingest import split_front_matter

    _, body = split_front_matter(text)
    match = _BODY.match(body)
    if match is None:
        raise RevisionError("the body must be exactly one ```json fenced block and nothing else")
    try:
        data = json.loads(match[1], object_pairs_hook=_refuse_duplicates)
    except ValueError as exc:
        raise RevisionError(f"the change set is not JSON: {exc}") from exc
    try:
        return ChangeSet.model_validate(data)
    except ValidationError as exc:
        raise RevisionError(f"the change set is not valid: {first_error(exc)}") from exc


def unchanged(worktree: Path, rel: str) -> str | None:
    """The rationale of the change set at `rel` in `worktree` when it proposes
    no change, or None: no file, one that does not read, or one proposing a
    change -- each of which a person at the gate has to see. Read the way the
    gate's own artifact is (`read_worktree_file`: contained, no symlink)."""
    from kraft.worker.worktree_read import read_worktree_file

    read, _ = read_worktree_file(worktree, rel, _MAX_BYTES)
    if read is None:
        return None
    try:
        changes = parse(read.text)
    except RevisionError:
        return None
    return changes.rationale if changes.empty else None


#: More than any change set a person could review at a gate; a larger file is
#: cut off, fails to parse, and so reaches a person.
_MAX_BYTES = 1_000_000


def revise(
    chain: MaterializedChain, changes: ChangeSet, *, gate: str, library: TemplateLibrary | None
) -> MaterializedChain:
    """`chain` with `changes` applied, or `RevisionError` naming why not.

    Only the nodes after `gate` -- the revision's own gate, the node the item
    stands on when it is approved -- may change: everything at or before it has
    run. A gate never changes, whatever its position. Every added node comes out
    of `library` by `extends`, and the result is re-validated whole, by the
    model intake uses: `Chain`'s own rules (unique ids, backward reject and
    restart targets, nothing published before the final gate), each override
    through the retry override's bounds (`validate_retry_override`: the
    ratchet, the maxima, a cap under its parent's), and every scope under the
    item's own policy (`check_scopes`). So an invalid change set never reaches
    the chain: nothing here writes anything.
    """
    if changes.empty:
        return chain
    ids = [n.id for n in chain.chain.nodes]
    if gate not in ids:
        raise RevisionError(f"this chain has no gate {gate!r}")
    frozen = set(ids[: ids.index(gate) + 1])

    def open_node(nodes: tuple[ResolvedNode, ...], id: str, what: str) -> ResolvedNode:
        node = next((n for n in nodes if n.id == id), None)
        if node is None:
            raise RevisionError(f"{what}: this chain has no node {id!r}")
        if id in frozen:
            raise RevisionError(
                f"{what}: {id!r} is at or before {gate!r} and has already run; "
                "a revision changes only the nodes after it"
            )
        if isinstance(node.node, GateNode):
            raise RevisionError(
                f"{what}: {id!r} is a gate, which a revision never skips or changes"
            )
        return node

    skipped = set()
    for skip in changes.skip:
        node = open_node(chain.chain.nodes, skip.node, f"skip {skip.node}")
        if not node.node.skippable:
            raise RevisionError(f"skip {skip.node}: {skip.node!r} is not skippable")
        if _lands(node):
            raise RevisionError(
                f"skip {skip.node}: {skip.node!r} is part of the merge request's life (opening, "
                "describing, syncing, readying, merging it or waiting on its checks), which a "
                "revision never removes"
            )
        skipped.add(skip.node)
    after: dict[str, list] = {}
    steering = chain.chain.steering
    if changes.add:
        added, added_steering = _added_nodes(changes.add, library)
        for add, node in zip(changes.add, added, strict=True):
            # Right after the revision's own gate is the earliest a node can run.
            if add.after not in ids:
                raise RevisionError(f"add {add.node.id}: this chain has no node {add.after!r}")
            if add.after in frozen and add.after != gate:
                raise RevisionError(
                    f"add {add.node.id}: {add.after!r} is before {gate!r} and has already run; "
                    "a revision adds nodes only after it"
                )
            if add.after in skipped:
                raise RevisionError(
                    f"add {add.node.id}: it follows {add.after!r}, which is skipped"
                )
            after.setdefault(add.after, []).append(node)
        if added_steering:
            steering = {**added_steering, **(steering or {})}
    nodes = []
    for node in chain.chain.chain.nodes:
        if node.id not in skipped:
            nodes.append(node)
        nodes += after.get(node.id, [])
    try:
        # Read back the way a stored snapshot is (`FROZEN`): the nodes already
        # in it were validated when it was filed, and the added ones just were.
        authored = Chain.model_validate_json(
            chain.chain.chain.model_copy(update={"nodes": nodes}).model_dump_json(),
            context=FROZEN,
        )
    except ValidationError as exc:
        raise RevisionError(f"the revised chain does not validate: {first_error(exc)}") from exc
    revised = replace(chain, chain=ResolvedChain.from_chain(authored, steering=steering))
    for path, override in changes.overrides.items():
        open_node(revised.chain.nodes, path.split(PATH_SEPARATOR)[0], f"override {path}")
        try:
            revised = validate_retry_override(
                revised, path, task_config=override.task_config(), policy=override.policy()
            ).chain
        except RetryOverrideError as exc:
            raise RevisionError(f"override {path}: {exc}") from exc
    try:
        for base in (revised.policy, *revised.repository_policies.values()):
            revised.chain.check_scopes(base, revised.item_policy)
    except PolicyError as exc:
        raise RevisionError(f"the revised chain's policy does not resolve: {exc}") from exc
    if (refusal := revised.sandbox_refusal()) is not None:
        raise RevisionError(refusal)
    if chain.untrimmed is not None:
        # The chain before its attachment trim (Kraft-s7c04.29) gets the same
        # change set: a revision is not a trim, so the one invariant that copy
        # keeps -- it is this chain with the trimmed nodes back -- still holds.
        # Only nodes before the revision gate are ever trimmed, and a
        # revision touches none of those, so it applies there unchanged.
        whole = replace(
            chain,
            chain=ResolvedChain.from_chain(chain.untrimmed, steering=chain.chain.steering),
            untrimmed=None,
        )
        revised = replace(
            revised, untrimmed=revise(whole, changes, gate=gate, library=library).chain.chain
        )
    return revised


#: The artifact a merge request's title, body and labels come from
#: (`forge.mr.read_mr_meta`).
_MR_META = "mr_meta"


def _lands(node: ResolvedNode) -> bool:
    """Whether `node` does part of the work of landing the change (Kraft-eh5as):
    any forge task -- open, sync, ready, merge, or a wait on CI, review or
    approval -- or the description the merge request opens with. Keyed on what
    the node runs, never its id, so a custom chain's own names are held too.
    Every step container counts, recovery included (Kraft-8cu5r): unlike
    `ResolvedNode.produces`, which names what a node is for, this asks whether
    skipping it could drop landing work, and a node that syncs its merge
    request only when it fails still does that work.
    A skip would let an item "complete" with nothing landed."""
    return any(
        isinstance(t.task, ForgeTask) or getattr(t.task, "produces", None) == _MR_META
        for step in node.steps_in()
        for t in step.tasks
    )


def _added_nodes(
    adds: list[Add], library: TemplateLibrary | None
) -> tuple[list[ExecNode], dict[str, str] | None]:
    """Each added node expanded out of `library`, by the resolution a chain
    file gets (`TemplateLibrary.resolve_chain`: `extends`, steering, skills, a
    node agreeing on what it produces), with the steering text it selects."""
    if library is None:
        raise RevisionError("adding a node needs the template library, and none is loaded")
    try:
        candidate, id = library.with_chain(
            Path(f"{CHAIN_REVISION}.yaml"),
            {"id": CHAIN_REVISION, "nodes": [add.node.authored() for add in adds]},
        )
        resolved = candidate.resolve_chain(id)
    except TemplateLibraryError as exc:
        raise RevisionError(f"add: {exc}") from exc
    return [n.node for n in resolved.nodes], resolved.steering


def _fields(node: ResolvedNode) -> dict[str, dict]:
    """Each scope of `node` a revision can set -- the node's own policy, and
    every task -- flattened to `path.field` keys."""

    def flat(value: object, prefix: str) -> dict:
        if not isinstance(value, dict):
            return {prefix: value}
        return {k: v for key, sub in value.items() for k, v in flat(sub, f"{prefix}.{key}").items()}

    policy = node.node.policy.model_dump(mode="json", exclude_none=True) if node.node.policy else {}
    found = flat({"policy": policy}, node.id) if policy else {}
    for task in node.tasks():
        found |= flat(task.task.model_dump(mode="json", exclude_none=True), task.path)
    return found


def diff(before: ResolvedChain, after: ResolvedChain) -> list[str]:
    """The revision as lines: every node in order, `- ` for one skipped and
    `+ ` for one added, and under a kept node a `~ path.field: old -> new` line
    for each value an override changed."""
    old, new = [n.id for n in before.nodes], [n.id for n in after.nodes]
    by_old, by_new = {n.id: n for n in before.nodes}, {n.id: n for n in after.nodes}
    lines = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag != "equal":
            lines += [f"- {id}" for id in old[i1:i2]] + [f"+ {id}" for id in new[j1:j2]]
            continue
        for id in old[i1:i2]:
            lines.append(f"  {id}")
            was, now = _fields(by_old[id]), _fields(by_new[id])
            lines += [
                f"~ {key}: {was.get(key)!r} -> {now.get(key)!r}"
                for key in sorted(was.keys() | now.keys())
                if was.get(key) != now.get(key)
            ]
    return lines


#: What a person does with a proposal that cannot be applied.
_REJECT = "Approving it is refused; reject it back to the node that wrote it, with this reason."


def render(
    text: str, chain: MaterializedChain, gate: str, library: TemplateLibrary | None
) -> tuple[str, MaterializedChain | None]:
    """The gate's document for a person, and the revised chain it shows (None
    when it shows none): the rationale, each change with its evidence, every
    added node as it will run (resolved out of `library` now), then the diff --
    or, when the proposal cannot be read or applied, why not, which is also
    what the approval would refuse with."""
    try:
        changes = parse(text)
    except RevisionError as exc:
        return f"# Chain revision\n\n## Cannot be read\n\n**{exc}**\n\n{_REJECT}\n", None
    lines = ["# Chain revision", "", changes.rationale, "", "## Changes", ""]
    lines += [f"- skip `{s.node}` -- {s.evidence}" for s in changes.skip]
    lines += [f"- add `{a.node.id}` after `{a.after}` -- {a.evidence}" for a in changes.add]
    lines += [
        f"- set {', '.join(f'{k}={v}' for k, v in {**o.task_config(), **o.policy()}.items())} "
        f"on `{path}` -- {o.evidence}"
        for path, o in changes.overrides.items()
    ]
    if changes.empty:
        lines.append("None: the chain stays as it is.")
    try:
        revised = revise(chain, changes, gate=gate, library=library)
    except RevisionError as exc:
        return "\n".join(
            [*lines, "", "## Cannot be applied", "", f"**{exc}**", "", _REJECT, ""]
        ), None
    added = {a.node.id for a in changes.add}
    for node in revised.chain.nodes:
        if node.id in added:
            authored = node.node.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            lines += ["", f"### `{node.id}`, as it will run", "", "```json"]
            lines += [json.dumps(authored, indent=1), "```"]
    lines += ["", "## Diff", "", "```diff", *diff(chain.chain, revised.chain), "```", ""]
    return "\n".join(lines), revised


def digest(revised: MaterializedChain) -> str:
    """What a person approving a revision was shown, as one value: the whole
    revised chain, added nodes resolved (Kraft-ze1yj). The diff alone would not
    do -- it names an added node without what it runs."""
    return hashlib.sha256(revised.to_json().encode()).hexdigest()


def artifact_digest(
    chain: MaterializedChain, gate: str, text: str, *, library: TemplateLibrary | None = None
) -> str | None:
    """What `gate`'s chain-revision artifact currently resolves to, or None
    when it cannot be parsed or applied, or resolves to no change -- an
    unresolvable or empty proposal binds nothing, the same as `_revise`'s own
    approval check (`kraft.api.routes.gates`).

    The same three functions that check, applies whole (`parse`, `revise`,
    `digest`), exposed so a reader of the artifact *before* approval -- an
    agent's own gate review (Kraft-rndd1) -- can bind its verdict to exactly
    what it read, the way a person's approval already binds to what `GET
    .../artifact` rendered for them.
    """
    try:
        changes = parse(text)
        revised = revise(chain, changes, gate=gate, library=library)
    except RevisionError:
        return None
    if revised is chain:
        return None
    return digest(revised)


class StaleRevision(RevisionError):
    """The revision would apply something other than what its gate showed: the
    library changed between the two (Kraft-ze1yj)."""


def context_note(chain: MaterializedChain | None, node_id: str) -> str:
    """What a task producing a `chain_revision` is shown beyond its prompt: the
    nodes it may change, as authored, and where the frozen part ends -- at the
    first revision gate after `node_id`, the node it runs in."""
    if chain is None:
        return ""
    nodes = chain.chain.nodes
    here = next((i for i, n in enumerate(nodes) if n.id == node_id), 0)
    gate = next(
        (
            i
            for i, n in enumerate(nodes[here:], here)
            if isinstance(n.node, GateNode) and n.node.artifact == CHAIN_REVISION
        ),
        here,
    )
    tail = [
        {
            "node": n.node.model_dump(mode="json", exclude_none=True, exclude_defaults=True),
            "task_paths": [t.path for t in n.tasks()],
        }
        for n in nodes[gate + 1 :]
    ]
    return (
        f"\n\nThe nodes up to and including `{nodes[gate].id}` have run or are running, and a "
        "revision may not change them. The nodes after it, which it may change, as authored:\n"
        f"```json\n{json.dumps(tail, indent=1)}\n```\n"
    )
