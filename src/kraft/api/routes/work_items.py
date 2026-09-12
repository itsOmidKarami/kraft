from __future__ import annotations

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
from kraft.api.routes import board
from kraft.executor import entry
from kraft.templates import ATTACHMENT_GATES, materialize, validate_agent_overrides


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
    #: cross-repo (design 1g "Advanced · cross-repo"): submodule paths from the
    #: repo's .gitmodules, and what happens to the root pointer when they land
    submodules: list[str] = []
    root_merge_policy: str = "bump"
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
    #: Node ids to drop from the materialized chain at intake (UI v2 · 04
    #: point 6; design 10/m09's click-to-skip). Rejected (422) if any name
    #: is not a node of the resolved template. A gated node may be named --
    #: see `templates.materialize`'s docstring for why that is not a bypass.
    skip_nodes: list[str] = []
    #: Per-item spend cap at intake (point 6), same presence-vs-null rule as
    #: the PATCH route: omitted means "use the policy default", `null` means
    #: an explicit "no cap", a number means that cap. Distinguished via
    #: `model_fields_set`, same as `WorkItemPatch.budget_usd`.
    budget_usd: float | None = None
    #: Per-node overrides at intake, same shape and field set as the PATCH
    #: route's `node_overrides` (point 6): `{node_id: {auto_escalate: bool}}`.
    node_overrides: dict[str, dict] = {}


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
    template = st.templates.valid.get(
        body.chain_template if body.chain_template is not None else "default"
    )
    if template is None:
        raise HTTPException(422, "unknown or invalid template")
    if body.root_merge_policy not in store.ROOT_MERGE_POLICIES:
        raise HTTPException(422, f"unknown root_merge_policy {body.root_merge_policy!r}")
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
    node_ids = {n["id"] for n in template.nodes}
    unknown_skip = set(body.skip_nodes) - node_ids
    if unknown_skip:
        raise HTTPException(422, f"unknown node id(s) to skip: {sorted(unknown_skip)}")
    # A kept node's rebase_bounce_to naming a skipped node is a dangling bounce
    # target: walk.py's `next(j for j, n in ... if n["id"] == bounce_to)` has no
    # fallback like `reject_target`'s and raises StopIteration mid-run, crashing
    # the executor into needs_human. Reject the skip at intake instead.
    dangling_bounce = {
        n["id"]: n["rebase_bounce_to"]
        for n in template.nodes
        if n["id"] not in body.skip_nodes and n.get("rebase_bounce_to") in body.skip_nodes
    }
    if dangling_bounce:
        raise HTTPException(
            422,
            f"cannot skip node(s) {sorted(set(dangling_bounce.values()))}: named as "
            f"rebase_bounce_to by kept node(s) {sorted(dangling_bounce)}",
        )
    # skip_nodes alone, or together with an attachment's gate trim, must not
    # empty the chain: materialize would hand `intake` nothing to run, and
    # `create_work_item`/`executor.run_once` both index `nodes[0]` unguarded
    # (code-review). The bead is already filed and the run spawned by the
    # time either of those would crash, so this has to be checked first.
    satisfied = frozenset(ATTACHMENT_GATES[a["kind"]] for a in attachments)
    if not materialize(template, satisfied_gates=satisfied, skip_nodes=body.skip_nodes)["nodes"]:
        raise HTTPException(422, "skip_nodes would leave no nodes in the chain")
    for node_id, fields in body.node_overrides.items():
        if node_id not in node_ids:
            raise HTTPException(422, f"unknown node id {node_id!r}")
        extra = set(fields) - store.OVERRIDABLE_NODE_FIELDS
        if extra:
            raise HTTPException(422, f"node {node_id!r}: cannot override {sorted(extra)}")
        if "auto_escalate" in fields and not isinstance(fields["auto_escalate"], bool):
            raise HTTPException(422, f"node {node_id!r}: auto_escalate must be a boolean")
        if "auto_escalate_stuck" in fields and not isinstance(fields["auto_escalate_stuck"], bool):
            raise HTTPException(422, f"node {node_id!r}: auto_escalate_stuck must be a boolean")
    try:
        wid = await executor.intake(
            st.db,
            st.run_dirs,
            title=body.title,
            description=body.description,
            repo=body.repo,
            template=template,
            # The raw request value, not the resolved template's id (Kraft-cd47):
            # None here means no explicit template was chosen, and must stay
            # None in the row -- `intake`'s own default would otherwise store
            # `template.id`, indistinguishable from an item that named
            # `chain_template: "default"` outright.
            chain_template=body.chain_template,
            bd_cwd=deps.bd_cwd(),
            submodules=body.submodules,
            root_merge_policy=body.root_merge_policy,
            attachments=attachments,
            status="active" if body.autostart else "paused",
            auto_gate=body.auto_gate,
            skip_nodes=frozenset(body.skip_nodes),
            budget_set="budget_usd" in body.model_fields_set,
            budget_usd=body.budget_usd,
            node_overrides=body.node_overrides or None,
        )
    except Exception as exc:  # noqa: BLE001 -- executor.intake raises several unrelated types
        # No longer reachable for a bd failure — `executor.intake` degrades
        # instead (Kraft-7gy). A 502 here is now a template or a DB problem.
        raise HTTPException(502, f"intake failed: {exc}") from exc

    # Present only when there is one: a null field on every successful create is
    # noise in `kraft item create`'s kv block and in the API.
    warning = deps._bead_warning(st, wid)
    extra = {"bead_warning": warning} if warning else {}

    if not body.autostart:
        # Created, not started. `/resume` begins it at node zero, because a NULL
        # current_node_id falls through that handler's `next(..., 0)` default.
        return {"id": wid, "status": "paused", **extra}

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
                registry=st.registry,
                bd_cwd=deps.bd_cwd(),
                policy=st.policy,
                launch=deps.launch(st, body.repo),
                on_approve=deps._on_approve(st),
            ),
        ),
    )
    row = st.db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    chain = json.loads(row["chain_definition"])
    return JSONResponse(
        status_code=201,
        content={
            "id": wid,
            "bead_id": row["bead_id"],
            "status": row["status"],
            "chain_definition": chain,
            # the run task advances this asynchronously; before its first write the
            # chain still starts at node 0 by definition.
            "current_node_id": row["current_node_id"] or chain["nodes"][0]["id"],
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
    template = st.templates.valid.get(
        body.chain_template if body.chain_template is not None else "default"
    )
    if template is None:
        raise HTTPException(422, "unknown or invalid template")
    if len(body.title) > beads_mod.MAX_TITLE:
        raise HTTPException(
            422,
            f"title is {len(body.title)} characters; the tracker's limit is {beads_mod.MAX_TITLE}",
        )
    if not Path(body.repo).is_dir():
        raise HTTPException(422, f"repo path does not exist: {body.repo}")
    try:
        wid = await executor.intake(
            st.db,
            st.run_dirs,
            title=body.title,
            description=body.description,
            repo=body.repo,
            template=template,
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


def _validate_node_overrides(st, row, patch: dict[str, dict]) -> None:
    """422 on an unknown node id or field, 409 on a node that has started
    (UI v2 · 04 point 1). `patch == {}` (reset to template) 409s if the item
    has started at all -- point 2, "only allowed on unstarted nodes".
    """
    chain = json.loads(row["chain_definition"])
    node_ids = {n["id"] for n in chain["nodes"]}
    if not patch:
        if row["current_node_id"] is not None:
            raise HTTPException(409, "work item has already started; overrides cannot be reset")
        return

    def check(c):
        for node_id, fields in patch.items():
            if node_id not in node_ids:
                raise HTTPException(422, f"unknown node id {node_id!r}")
            extra = set(fields) - store.OVERRIDABLE_NODE_FIELDS
            if extra:
                raise HTTPException(422, f"node {node_id!r}: cannot override {sorted(extra)}")
            if "auto_escalate" in fields and not isinstance(fields["auto_escalate"], bool):
                raise HTTPException(422, f"node {node_id!r}: auto_escalate must be a boolean")
            if "auto_escalate_stuck" in fields and not isinstance(
                fields["auto_escalate_stuck"], bool
            ):
                raise HTTPException(422, f"node {node_id!r}: auto_escalate_stuck must be a boolean")
            if store.node_started(c, row["id"], node_id):
                raise HTTPException(409, f"node {node_id!r} has started; its config is locked")

    st.db.read(lambda c: check(c))


@api_router.patch("/work-items/{wid}")
async def update_work_item(wid: str, body: WorkItemPatch, request: Request):
    st = request.app.state
    row = deps._work_item_row(st, wid)  # 404s on an unknown work item, before any 422
    fields_set = body.model_fields_set
    if (
        body.title is None
        and body.description is None
        and body.chain_template is None
        and body.agent_overrides is None
        and body.node_overrides is None
        and "budget_usd" not in fields_set
    ):
        raise HTTPException(
            422,
            "nothing to patch: send a title, description, chain_template, agent_overrides, "
            "node_overrides, or budget_usd",
        )
    if body.title is not None and not body.title.strip():
        raise HTTPException(422, "title cannot be empty")

    if body.node_overrides is not None:
        _validate_node_overrides(st, row, body.node_overrides)

    new_chain_definition = None
    if body.chain_template is not None:
        template = st.templates.valid.get(body.chain_template)
        if template is None:
            raise HTTPException(404, f"unknown chain template {body.chain_template!r}")
        if row["current_node_id"] is not None:
            raise HTTPException(
                409, "work item has already started; template is fixed for its life"
            )
        # The exact expression `executor.intake` uses today, against the
        # item's existing attachments -- switching template must not force a
        # re-attach to get back a trim the item already earned.
        satisfied = frozenset(ATTACHMENT_GATES[a["kind"]] for a in entry.attachments_of(row))
        new_chain_definition = json.dumps(materialize(template, satisfied_gates=satisfied))

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
        if body.chain_template is not None:
            store.set_chain_template(c, wid, body.chain_template, new_chain_definition)
        if body.agent_overrides is not None:
            store.set_agent_overrides(
                c, wid, json.dumps(body.agent_overrides) if body.agent_overrides else None
            )
        if body.node_overrides is not None:
            store.set_node_overrides(c, wid, body.node_overrides)
        if "budget_usd" in fields_set:
            store.set_budget(c, wid, body.budget_usd)

    await st.db.write(apply)
    # `model_dump(exclude_none=True)` would drop an explicit `budget_usd:
    # null` along with every untouched field, so build the echo from
    # `fields_set` (what the caller actually sent) instead.
    return {"id": wid, **{f: getattr(body, f) for f in fields_set}}
