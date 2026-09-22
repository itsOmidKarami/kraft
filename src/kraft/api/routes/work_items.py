from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kraft import config as config_mod
from kraft import executor, store
from kraft.adapters import beads as beads_mod
from kraft.api import api_router, deps
from kraft.api.routes import board, gates
from kraft.executor import entry
from kraft.overrides import validate_agent_overrides, validate_node_override_fields
from kraft.policy import PolicyError, PolicyMaximaInput
from kraft.templates.environment import RootPointerPolicy
from kraft.templates.models import MaterializedChain, ResolvedChain


class Attachment(BaseModel):
    kind: Literal["spec", "plan"]
    #: repo-relative; validated and normalized server-side before it is stored
    path: str


class NewWorkItem(BaseModel):
    title: str
    #: the brief — prose, and what the spec node writes a design from. A title is
    #: only a label.
    description: str = ""
    repo: str
    #: None means no explicit template was chosen (Kraft-cd47) -- resolved to
    #: the `default` template below, at lookup time, and stored as None so it
    #: stays distinguishable from an item that named `chain_template:
    #: "default"` outright.
    chain_template: str | None = None
    #: A workspace item (design 1g "Advanced · cross-repo"): the workspace
    #: `repo` is the root of, the members it selects, and the root-pointer
    #: policy -- the workspace's `root_pointer_default` when unset. Frozen into
    #: the item's target at intake (`deps.workspace_target`).
    workspace: str | None = None
    members: list[str] = []
    root_pointer_policy: RootPointerPolicy | None = None
    #: The branch the item's work starts from and its merge request targets
    #: (Kraft-v9gbi), frozen into its target; a workspace item's root's only.
    #: None is the repository's default branch. Refused (422) unless origin
    #: has it.
    base_branch: str | None = None
    #: spec/plan documents that already exist — they trim the gates they satisfy
    attachments: list[Attachment] = []
    #: the caller's working directory, sent only when there are attachment paths
    #: to resolve. A local CLI or MCP caller is often standing in a worktree of
    #: `repo` — for a Kraft worker, always — and that is where the document it
    #: wants to hand over actually is (Kraft-85wk). The browser sends nothing.
    cwd: str | None = None
    #: False creates the item without running it (design §6 rule 1). An agent
    #: cannot spend tokens unattended; a human starts it from the board.
    autostart: bool = True
    #: Arms agent gate review for this item's `auto_escalate` gates
    #: (Kraft-zr3s). On by default; `--no-auto-gate` opts out per item.
    auto_gate: bool = True
    #: Bead ids this item implements, closed on completion. The description is
    #: no longer parsed for them: naming a bead in prose promises nothing.
    implements_beads: list[str] = []
    #: Node ids to drop from the materialized chain at intake (UI v2 · 04
    #: point 6; design 10/m09's click-to-skip). Rejected (422) if any name
    #: is not a node of the resolved template. A gated node may be named --
    #: see `ResolvedChain.materialize`'s docstring for why that is not a bypass.
    skip_nodes: list[str] = []
    #: Per-item spend cap at intake (point 6), same presence-vs-null rule as
    #: the PATCH route: omitted means "use the policy default", `null` means
    #: an explicit "no cap", a number means that cap. Distinguished via
    #: `model_fields_set`, same as `WorkItemPatch.budget_usd`.
    budget_usd: float | None = None
    #: Per-node overrides at intake, same shape and field set as the PATCH
    #: route's `node_overrides` (point 6): `{node_id: {auto_escalate: bool}}`.
    node_overrides: dict[str, dict] = {}
    #: The item's own policy override (Kraft-ab1bh, `policy.WorkItemPolicy`):
    #: item-wide policy fields, plus `paths: {canonical path: {field: value}}`
    #: for one node, step or task. 422 naming the field it refuses.
    policy: dict | None = None


def _git_common_dir(path: Path) -> Path | None:
    """The `.git` that every working tree of one repository shares, or None.

    Identical for a main checkout and every worktree linked to it; different for
    an unrelated repo, and different for a submodule of this one (whose common
    dir is `<super>/.git/modules/<path>`). `config_mod.git_read` never raises,
    so a directory that is not a repo, or is not readable, is None rather than a
    500.
    """
    common = config_mod.git_read(
        path, "rev-parse", "--path-format=absolute", "--git-common-dir", expected_failure=True
    )
    return Path(common).resolve() if common else None


def _attachment_roots(repo: str, cwd: str | None) -> list[Path]:
    """The roots an attachment path may be resolved against, in order.

    The registered repo always, and first. The caller's own working tree second,
    and only when it proves it is a working tree of that same repository —
    same `.git`, or no second root (Kraft-85wk). With no `cwd` the reachable set
    is exactly what it has always been.

    This check is also what enforces Kraft-vwv's decision: an intake attachment
    belongs to the item's root worktree only. A `cwd` inside a submodule
    contributes no root, because a submodule's common dir is not the
    superproject's — so a submodule-local spec cannot be attached by accident,
    and `ensure_worktree` copies into the root worktree only. One change has one
    spec; N copies in the MR would be N places for it to drift.
    """
    root = Path(repo).resolve()
    if not cwd:
        return [root]
    common = _git_common_dir(Path(cwd))
    if common is None or common != _git_common_dir(root):
        return [root]
    toplevel = config_mod.git_read(Path(cwd), "rev-parse", "--show-toplevel", expected_failure=True)
    if toplevel is None:
        return [root]
    top = Path(toplevel).resolve()
    return [root] if top == root else [root, top]


def _validated_attachments(
    repo: str, attachments: list[Attachment], cwd: str | None = None
) -> list[dict]:
    """Trust boundary: `path` comes from a browser or a local agent and is used
    to read a file and to write into a worktree. Resolve under a candidate root
    and reject any escape.

    The widening `cwd` buys is small and proven: other working trees of the same
    repository, for a caller the perimeter middleware has already established is
    local and same-site. A path is taken at the first root it both stays inside
    and exists under.
    """
    kinds = [a.kind for a in attachments]
    if len(set(kinds)) != len(kinds):
        raise HTTPException(422, "at most one attachment per kind")
    roots = _attachment_roots(repo, cwd)
    out = []
    for a in attachments:
        inside = False
        for root in roots:
            target = (root / a.path).resolve()
            if not target.is_relative_to(root):
                continue
            inside = True
            # Working tree, not HEAD: a document written minutes ago is legal
            # input, and ensure_worktree copies it into the worktree.
            if not target.is_file():
                continue
            entry = {"kind": a.kind, "path": str(target.relative_to(root))}
            if root != roots[0]:
                # The copy reads `repo / path` by default and this file is not
                # in `repo` at all, so it would silently copy nothing and leave
                # a trimmed gate with no document. Absolute, so the copy needs
                # to know nothing about roots.
                entry["source"] = str(target)
            out.append(entry)
            break
        else:
            raise HTTPException(
                422,
                f"attachment not found: {a.path}"
                if inside
                else f"attachment path escapes the repo: {a.path}",
            )
    return out


@api_router.post("/work-items", status_code=201)
async def create_work_item(body: NewWorkItem, request: Request):
    st = request.app.state
    if st.invalid_policy:
        # Spec §9: a malformed policy.yaml makes the process refuse work, same
        # posture as an invalid registry — do not accept a run we cannot bound.
        detail = "; ".join(st.invalid_policy)
        raise HTTPException(503, f"policy config invalid, refusing work: {detail}")
    if body.autostart and gates._decided_by(request) != "human":
        # Design §6 rule 1, at the one door every client reaches (Kraft-s7c04.31):
        # an agent files work, a human starts it. Refused, not quietly filed
        # paused, so the caller learns that nothing is running.
        raise HTTPException(
            403,
            "an agent cannot start the work it files: file it paused (no autostart) "
            "and a human starts it from the board",
        )
    chain = deps.resolve_chain_or_422(st, body.chain_template)
    # Before `executor.intake`, which no longer 502s on a bd failure (Kraft-7gy)
    # and would file the item with no bead and a warning nobody reads. An
    # explicit check rather than `Field(max_length=...)`: pydantic's 422 body is
    # a list of error dicts, and `kraft item create` prints `detail` straight
    # through -- one sentence is the contract every other CLI error keeps.
    if len(body.title) > beads_mod.MAX_TITLE:
        raise HTTPException(
            422,
            f"title is {len(body.title)} characters; the tracker's limit is {beads_mod.MAX_TITLE}",
        )
    if not Path(body.repo).is_dir():
        raise HTTPException(422, f"repo path does not exist: {body.repo}")
    attachments = _validated_attachments(body.repo, body.attachments, body.cwd)
    attachment_kinds = frozenset(a["kind"] for a in attachments)
    node_ids = {n.id for n in chain.nodes}
    unknown_skip = set(body.skip_nodes) - node_ids
    if unknown_skip:
        raise HTTPException(422, f"unknown node id(s) to skip: {sorted(unknown_skip)}")
    # A skip and an attachment trim must not empty the chain between them:
    # `create_work_item`/`executor.run_once` both index `nodes[0]` unguarded,
    # and by the time either would crash the bead is filed and the run spawned.
    # One dry-run drop, because that is what `materialize` will do -- two
    # separate emptiness checks can each pass while their union empties it.
    # (The legacy `rebase_bounce_to` dangling-target check is gone with the
    # field: Task 4a deleted `walk.bounce`, and a V1 gate's `reject_to` is
    # nulled rather than left dangling by the same drop.)
    # The repository layer, once: the dry run and the intake below must
    # materialize from the same policy (`repository-policy-cannot-relax-
    # instance-safety`).
    base_branch = await deps.base_branch_or_422(body.repo, body.base_branch)
    target = deps.workspace_target(
        st,
        body.repo,
        workspace=body.workspace,
        members=body.members,
        root_pointer_policy=body.root_pointer_policy,
        base_branch=base_branch,
    ) or entry.single_repo_target(body.repo, base_branch=base_branch)
    policy = deps.item_policy_or_422(st, body.repo, target)
    per_repository = deps.repository_policies_or_422(st, target)
    try:
        item_policy = (
            chain.materialize(
                target=target,
                effective_policy=policy,
                repository_policies=per_repository,
                attachment_kinds=attachment_kinds,
                skip_nodes=frozenset(body.skip_nodes),
            )
            .with_item_policy(body.policy)
            .item_policy
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _check_node_overrides(
        body.node_overrides,
        node_ids,
        {n.id: n.auto_review for n in chain.nodes},
        deps.instance_policy(st).maxima,
    )
    # Read before this item exists, so it cannot find itself. Warned, not
    # refused (Kraft-s7c04.30): a deliberate second item is legitimate, and
    # the usual reason to re-file, a revised spec, now has its own door.
    duplicates = st.db.read(
        lambda c: store.open_duplicates(c, body.repo, body.title, body.implements_beads)
    )
    try:
        wid = await executor.intake(
            st.db,
            st.run_dirs,
            title=body.title,
            description=body.description,
            repo=body.repo,
            chain=chain,
            effective_policy=policy,
            # The raw request value, not the resolved template's id (Kraft-cd47):
            # None here means no explicit template was chosen, and must stay
            # None in the row -- `intake`'s own default would otherwise store
            # `template.id`, indistinguishable from an item that named
            # `chain_template: "default"` outright.
            chain_template=body.chain_template,
            bd_cwd=deps.bd_cwd(),
            target=target,
            repository_policies=per_repository,
            attachments=attachments,
            status="active" if body.autostart else "paused",
            # Folded into intake's own INSERT transaction, not a separate
            # `active_count` read here: two autostart creates racing a few
            # milliseconds apart must not both see a free slot and both win
            # one (Kraft-m43g, Kraft-nxht). `None` when not autostarting --
            # `status` is already "paused" and needs no capacity decision.
            limit=(st.policy.max_concurrent if st.policy else 1) if body.autostart else None,
            auto_gate=body.auto_gate,
            implements_beads=body.implements_beads,
            skip_nodes=frozenset(body.skip_nodes),
            budget_set="budget_usd" in body.model_fields_set,
            budget_usd=body.budget_usd,
            node_overrides=body.node_overrides or None,
            policy_override=item_policy.model_dump(exclude_none=True, exclude_defaults=True)
            if item_policy is not None
            else None,
        )
    except Exception as exc:  # noqa: BLE001 -- executor.intake raises several unrelated types
        # No longer reachable for a bd failure — `executor.intake` degrades
        # instead (Kraft-7gy). A 502 here is now a template or a DB problem.
        raise HTTPException(502, f"intake failed: {exc}") from exc

    # Present only when there is one: a null field on every successful create is
    # noise in `kraft item create`'s kv block and in the API.
    warning = deps._bead_warning(st, wid)
    extra = {"bead_warning": warning} if warning else {}
    if duplicates:
        extra["duplicate_warning"] = (
            "looks like open work item "
            + "; ".join(f"{r['id']} ({r['status']}, {why})" for r, why in duplicates)
            + ". To revise its spec or plan, use `kraft item set-attachments` on it "
            "rather than filing again; abandon whichever of the two is not wanted"
        )

    if not body.autostart:
        # Created, not started. `/resume` begins it at node zero, because a NULL
        # current_node_id falls through that handler's `next(..., 0)` default.
        return {"id": wid, "status": "paused", **extra}

    row = st.db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    if row["status"] != "active":
        # Lost the capacity race inside `intake`'s own INSERT (Kraft-m43g,
        # Kraft-nxht): landed "paused" same as an explicit `not autostart`,
        # not rejected -- there is no walk to spawn.
        return {"id": wid, "status": row["status"], **extra}

    deps.spawn(
        request.app,
        wid,
        deps.guard(
            st.db,
            wid,
            executor.run(
                st.db,
                st.run_dirs,
                work_item_id=wid,
                bd_cwd=deps.bd_cwd(),
                policy=st.policy,
                launch=deps.launch(st, body.repo),
                on_approve=deps._on_approve(st),
            ),
        ),
    )
    # The chain as filed: the trim the attachments earned is already applied,
    # so this is what will actually run and not what the template declares.
    filed = store.materialized_chain_of(row)
    return JSONResponse(
        status_code=201,
        content={
            "id": wid,
            "bead_id": row["bead_id"],
            "status": row["status"],
            # `store.chain_view`, like both GET doors: this returned `{}` for a
            # V1 item, so `kraft item create --json` printed a chain with no
            # nodes and this door contradicted the "one shape at every door"
            # rationale of the fix that changed the other two.
            "chain_definition": store.chain_view(row),
            "materialized_chain": row["materialized_chain"],
            # the run task advances this asynchronously; before its first write the
            # chain still starts at node 0 by definition.
            "current_node_id": row["current_node_id"]
            or (filed.chain.nodes[0].id if filed is not None else None),
            **extra,
        },
    )


class TriggerBody(BaseModel):
    repo: str
    title: str
    description: str = ""
    chain_template: str | None = None


@api_router.post("/triggers", status_code=201)
async def fire_trigger(body: TriggerBody, request: Request):
    """The HTTP twin of a policy.yaml cron trigger (Kraft-859) -- always
    paused, for the same reason: an agent cannot start work here any more
    than it can from manual intake or a cron tick."""
    st = request.app.state
    if st.invalid_policy:
        detail = "; ".join(st.invalid_policy)
        raise HTTPException(503, f"policy config invalid, refusing work: {detail}")
    chain = deps.resolve_chain_or_422(st, body.chain_template)
    if len(body.title) > beads_mod.MAX_TITLE:
        raise HTTPException(
            422,
            f"title is {len(body.title)} characters; the tracker's limit is {beads_mod.MAX_TITLE}",
        )
    if not Path(body.repo).is_dir():
        raise HTTPException(422, f"repo path does not exist: {body.repo}")
    policy = deps.item_policy_or_422(st, body.repo)
    try:
        wid = await executor.intake(
            st.db,
            st.run_dirs,
            title=body.title,
            description=body.description,
            repo=body.repo,
            chain=chain,
            effective_policy=policy,
            chain_template=body.chain_template,
            bd_cwd=deps.bd_cwd(),
            status="paused",
        )
    except Exception as exc:  # noqa: BLE001 -- executor.intake raises several unrelated types
        raise HTTPException(502, f"intake failed: {exc}") from exc
    # Always paused, so this always takes create_work_item's "not autostart"
    # branch — no _spawn, no executor.run. bead_warning is dropped: a trigger
    # has no CLI kv block reading it, and get_work_item below already
    # surfaces the item's real state.
    return await board.get_work_item(wid, request)


class WorkItemPatch(BaseModel):
    #: Absent means untouched, in every field. This route is still not a
    #: general table editor -- a body carrying anything else is ignored, not
    #: applied -- but a screen that edits one field must not blank another,
    #: so no field has a default that means "clear it" except where the field
    #: itself says otherwise. `description: ""` clears the brief;
    #: `description` omitted leaves it alone.
    title: str | None = None
    description: str | None = None
    #: A template name switches a not-yet-started item onto that template's
    #: own materialized chain (Kraft-gwn6). 404s on an unknown name; 409s once
    #: `current_node_id` is set -- the chain is fixed for the life of a
    #: started item.
    chain_template: str | None = None
    #: `None` (default) leaves the override alone. `{}` clears every field
    #: back to the template's own binding; a non-empty object *replaces* the
    #: whole stored override -- it does not merge with what is already there
    #: (Kraft-4k6l). No `current_node_id` restriction, unlike `chain_template`:
    #: a model/effort dial can change mid-chain, including on a paused item --
    #: that is the point, making a stuck item cheaper before its next retry.
    agent_overrides: dict | None = None
    #: Per-node field overrides (UI v2 · 04 point 1). `None` (default) leaves
    #: overrides alone. `{}` resets every node to the template -- refused
    #: (409) once the item has started. A non-empty object is per node id:
    #: `{node_id: {}}` drops that node's overrides, `{node_id: {field:
    #: value}}` sets fields on it -- merged into what's already stored, not a
    #: whole-object replace (unlike `agent_overrides`), so toggling one
    #: node's switch never wipes another's. Refused (409) for any node id
    #: that has already started.
    node_overrides: dict[str, dict] | None = None
    #: Per-item spend cap (point 4). Presence, not value, is what matters:
    #: omitted leaves the cap alone; sent as `null` sets an explicit "no
    #: cap"; sent as a number sets that cap. Distinguished via
    #: `model_fields_set` below, because `None` is both "untouched" and a
    #: legal explicit value here.
    budget_usd: float | None = None
    #: The item's own policy override, as at intake (`NewWorkItem.policy`).
    #: `None` (default) leaves it alone, `{}` clears it, and a non-empty
    #: object *replaces* the whole stored override. Accepted on any item that
    #: has not ended, running or waiting included. A change takes effect at
    #: the next node the item enters and at the next observation of a wait it
    #: is parked on: every node entry re-reads the row (`walk.walk_node`), and
    #: every observation is a fresh entry.
    policy: dict | None = None
    #: Per kind, like `node_overrides` (Kraft-s7c04.28): `{"spec": PATH}`
    #: re-snapshots that kind from PATH, `{"spec": null}` drops it and so
    #: restores the gate it trimmed (.29), and a kind not named keeps the copy
    #: it has. The chain is re-trimmed from the item's own snapshot, never
    #: the live template, so the trim follows the documents both ways and
    #: nodes skipped at intake stay skipped. 409 once `current_node_id` is
    #: set (a started item's worktree already holds its documents, committed
    #: on its branch, and its chain is fixed), or when another write changed
    #: the attachments or chain since this one read them.
    attachments: dict[Literal["spec", "plan"], str | None] | None = None
    #: The caller's working directory, as at intake (`NewWorkItem.cwd`).
    cwd: str | None = None


def _check_node_overrides(
    overrides: dict[str, dict],
    node_ids: set[str],
    reviewers: dict[str, object] | None,
    maxima: PolicyMaximaInput | None = None,
) -> None:
    """422 on an override naming no node of the chain, an unknown field, or an
    `auto_escalate: true` on a gate declaring no `auto_review`. One check for
    intake and `PATCH`, so the two doors cannot answer differently (review E
    #3). `reviewers` maps node id to its declared reviewer; `None` (a legacy
    row, with no typed chain) skips that last check.

    Said in the error, because that is what the caller reads. The override
    *permits or suppresses* a reviewing task the chain declares; it does not
    name one (`store.effective_nodes`). Accepting `auto_escalate: true` on a
    gate with no `auto_review` would persist a switch, answer 2xx, and change
    nothing -- the shape a human reads as "I turned it on".
    """
    for node_id, fields in overrides.items():
        if node_id not in node_ids:
            raise HTTPException(422, f"unknown node id {node_id!r}")
        field_errs = validate_node_override_fields(fields)
        if field_errs:
            raise HTTPException(422, f"node {node_id!r}: {field_errs[0]}")
        # A per-item fix-loop bound is an operational value, held to the
        # administrator maximum like the item's own policy override is
        # (Kraft-3br6j): the two doors must not bound it differently.
        for key, name, seconds in (
            ("attempts", "max_attempts", 1),
            ("wall_clock_s", "timeout_minutes", 60),
        ):
            ceiling = getattr(maxima, name, None)
            if (
                fields.get(key) is not None
                and ceiling is not None
                and fields[key] > ceiling * seconds
            ):
                raise HTTPException(
                    422,
                    f"node {node_id!r}: {key} {fields[key]} cannot exceed the administrator "
                    f"maximum {name} {ceiling}",
                )
        if (
            reviewers is not None
            and fields.get("auto_escalate") is True
            and reviewers.get(node_id) is None
        ):
            raise HTTPException(
                422,
                f"node {node_id!r} declares no 'auto_review' task, so agent gate review "
                f"cannot be switched on for it: an override permits or suppresses the "
                f"reviewer its chain declares, it cannot supply one",
            )


def _validate_node_overrides(st, row, patch: dict[str, dict]) -> None:
    """422 on an unknown node id or field, 409 on a node that has started
    (UI v2 · 04 point 1). `patch == {}` (reset to template) 409s if the item
    has started at all -- point 2, "only allowed on unstarted nodes".
    """
    # Node ids come from the shared reader, which answers for either chain shape
    # and never raises; only `reviewers` needs the typed snapshot, because
    # `auto_review` has no legacy equivalent.
    node_ids = set(store.chain_node_ids(row))
    v1 = store.materialized_chain_of(row)
    reviewers = {n.id: n.auto_review for n in v1.chain.nodes} if v1 is not None else None
    if not patch:
        if row["current_node_id"] is not None:
            raise HTTPException(409, "work item has already started; overrides cannot be reset")
        return

    _check_node_overrides(patch, node_ids, reviewers, deps.instance_policy(st).maxima)

    def check(c):
        for node_id in patch:
            if store.node_started(c, row["id"], node_id):
                raise HTTPException(409, f"node {node_id!r} has started; its config is locked")

    st.db.read(lambda c: check(c))


def _item_policy(row, patch: dict | None, new_materialized: str | None):
    """The item's own policy override after this PATCH, checked against the
    chain it will run: the new template's on a switch, else its own. A switch
    re-checks the override already stored, since a path it names may not
    exist on the new chain. 422 naming the field refused."""
    if patch is None and new_materialized is None:
        return None
    chain = (
        MaterializedChain.from_json(new_materialized)
        if new_materialized is not None
        else store.materialized_chain_of(row)
    )
    if chain is None:
        raise HTTPException(409, "this work item has no V1 chain to hold a policy override")
    try:
        # `{}` clears: nothing to check.
        wanted = store.policy_override_of(row) if patch is None else (patch or None)
        return chain.with_item_policy(wanted).item_policy
    except PolicyError as exc:
        raise HTTPException(422, str(exc)) from exc


def _retrimmed(row, filed_kinds: frozenset[str], kinds: frozenset[str]) -> str:
    """The item's own snapshot, re-trimmed for `kinds` (Kraft-2fyjt): never the
    live template, whose nodes, policy and steering may have changed since
    intake. Trimming needs only the snapshot; putting a trimmed node back needs
    the chain as it was before the trim, which a snapshot keeps as `untrimmed`
    from the moment anything is attached. 409 naming it when that is missing."""
    previous = store.materialized_chain_of(row)
    if previous is None:
        raise HTTPException(409, "this work item has no V1 chain to re-trim")
    untrimmed = previous.untrimmed if filed_kinds else previous.chain.chain
    if untrimmed is None and filed_kinds - kinds:
        raise HTTPException(
            409,
            f"this item's snapshot does not carry the nodes its "
            f"{', '.join(sorted(filed_kinds))} attachment trimmed at intake (it was filed "
            "before snapshots kept them), so dropping one cannot put them back; replace "
            "the document instead",
        )
    base = (
        ResolvedChain.from_chain(untrimmed, steering=previous.chain.steering)
        if untrimmed is not None
        else previous.chain
    )
    try:
        chain = base.trim_for_attachments(kinds)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return dataclasses.replace(
        previous, chain=chain, untrimmed=untrimmed if kinds else None
    ).to_json()


@api_router.patch("/work-items/{wid}")
async def update_work_item(wid: str, body: WorkItemPatch, request: Request):
    st = request.app.state
    # 404s on an unknown item and 409s on an ended one (Kraft-6vni1), before any 422
    row = deps._live_work_item_row(st, wid)
    fields_set = body.model_fields_set
    if (
        body.title is None
        and body.description is None
        and body.chain_template is None
        and body.agent_overrides is None
        and body.node_overrides is None
        and body.policy is None
        and body.attachments is None
        and "budget_usd" not in fields_set
    ):
        raise HTTPException(
            422,
            "nothing to patch: send a title, description, chain_template, agent_overrides, "
            "node_overrides, policy, attachments, or budget_usd",
        )
    if body.title is not None and not body.title.strip():
        raise HTTPException(422, "title cannot be empty")

    if body.node_overrides is not None:
        _validate_node_overrides(st, row, body.node_overrides)

    filed_kinds = frozenset(a["kind"] for a in entry.attachments_of(row))
    kinds, added = filed_kinds, []
    if body.chain_template is not None:
        # `st.library is None` first, through the shared door: a 404 "unknown
        # chain template 'default'" for a library that did not parse tells the
        # operator their chain id is wrong, which is the one misleading answer
        # the 503 was written to replace. The membership check answers 404 only
        # once there *is* a library to be absent from.
        deps.library_or_503(st)
        if body.chain_template not in st.library.chain_ids:
            raise HTTPException(404, f"unknown chain template {body.chain_template!r}")
        if row["current_node_id"] is not None:
            raise HTTPException(
                409, "work item has already started; template is fixed for its life"
            )
    if body.attachments is not None:
        if row["current_node_id"] is not None:
            raise HTTPException(
                409,
                "work item has already started; its worktree already holds the documents "
                "it was filed with, so its attachments are fixed for its life",
            )
        wanted = [Attachment(kind=k, path=p) for k, p in body.attachments.items() if p]
        added = _validated_attachments(row["repo"], wanted, body.cwd)
        kinds = (kinds - body.attachments.keys()) | {a["kind"] for a in added}

    new_materialized = None
    if body.chain_template is not None:
        # Re-materialized the way intake would have, attachments included: a
        # switch that re-authored a document the item already carries would
        # undo the trim it was filed with. The one door that reads the live
        # library: the caller asked for a different template.
        previous = store.materialized_chain_of(row)
        target = previous.target if previous is not None else None
        try:
            new_materialized = (
                deps.resolve_chain_or_422(st, body.chain_template)
                .materialize(
                    target=target or entry.single_repo_target(row["repo"]),
                    # The instance and repository layers, never
                    # `previous.policy`: that one already carries the old
                    # chain's own override, and layering the new chain's on
                    # top of it stacks the two (Kraft-yaq99).
                    effective_policy=deps.item_policy(st, row["repo"], target),
                    repository_policies=deps.repository_policies(st, target),
                    attachment_kinds=kinds,
                )
                .to_json()
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    elif body.attachments is not None:
        new_materialized = _retrimmed(row, filed_kinds, kinds)

    item_policy = _item_policy(row, body.policy, new_materialized)

    if body.agent_overrides is not None:
        errs = validate_agent_overrides(body.agent_overrides)
        if errs:
            raise HTTPException(422, errs[0])

    def apply(c):
        # One write, so a multi-field patch is one transaction and cannot land half.
        if body.title is not None:
            store.set_title(c, wid, body.title)
        if body.description is not None:
            store.set_description(c, wid, body.description)
        # Attachments first: its compare-and-set is against the row as read.
        if body.attachments is not None:
            store.set_attachments(
                c,
                wid,
                stored,
                new_materialized,
                seen=(row["attachments"], row["materialized_chain"]),
            )
        if body.chain_template is not None:
            store.set_chain_template(c, wid, body.chain_template, new_materialized)
        if body.agent_overrides is not None:
            store.set_agent_overrides(
                c, wid, json.dumps(body.agent_overrides) if body.agent_overrides else None
            )
        if body.node_overrides is not None:
            store.set_node_overrides(c, wid, body.node_overrides)
        if "budget_usd" in fields_set:
            store.set_budget(c, wid, body.budget_usd)
        if body.policy is not None:
            store.set_policy_override(c, wid, item_policy)

    filed = stored = entry.attachments_of(row)
    won = False
    try:
        if body.attachments is not None:
            try:
                stored = entry.replace_attachments(
                    st.run_dirs, wid, filed, body.attachments, added, repo=row["repo"]
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        await st.db.write(apply)
        won = True
    except ValueError as exc:
        if body.attachments is None:
            raise
        # `store.set_attachments`' compare-and-set lost: the item started, or
        # another PATCH changed its attachments or chain since `row` was read.
        raise HTTPException(409, f"{exc}; nothing was changed") from exc
    finally:
        # Only this request's own files: the superseded copies if its write
        # won, its fresh ones if not. Never "whatever the row does not name",
        # which would delete a concurrent PATCH's copies before it writes.
        if body.attachments is not None:
            keep, drop = (stored, filed) if won else (filed, stored)
            entry.discard_attachments(st.run_dirs, wid, drop, keep)
    # `model_dump(exclude_none=True)` would drop an explicit `budget_usd:
    # null` along with every untouched field, so build the echo from
    # `fields_set` (what the caller actually sent) instead.
    return {"id": wid, **{f: getattr(body, f) for f in fields_set}}
