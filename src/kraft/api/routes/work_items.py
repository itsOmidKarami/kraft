from __future__ import annotations

import dataclasses
import unicodedata
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import AliasChoices, BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError

from kraft import config as config_mod
from kraft import executor, storage, store
from kraft import policy as policy_mod
from kraft.adapters import beads as beads_mod
from kraft.api import SENTENCE_ERROR, api_router, deps
from kraft.api.routes import board, gates, lifecycle
from kraft.executor import entry
from kraft.overrides import (
    harness_refusal,
    item_harness_refusal,
    model_id_problem,
    validate_agent_overrides,
    validate_node_override_fields,
)
from kraft.policy import PolicyError, PolicyMaximaInput
from kraft.templates.environment import RootPointerPolicy
from kraft.templates.models import ExecNode, GateNode, MaterializedChain, ResolvedChain
from kraft.vocab import HOLDS_SLOT, WorkItemStatus


class Attachment(BaseModel):
    kind: Literal["spec", "plan"]
    #: repo-relative; validated and normalized server-side before it is stored
    path: str


def _one_chain(data: object) -> object:
    """`chain` and its pre-2.0 name `chain_template` are one field: a body
    naming two different chains is refused, not settled by which name wins,
    in one sentence naming both."""
    if (
        isinstance(data, dict)
        and data.get("chain") is not None
        and data.get("chain_template") is not None
        and data["chain"] != data["chain_template"]
    ):
        raise PydanticCustomError(
            SENTENCE_ERROR,
            "`chain` {chain} and `chain_template` {chain_template} name different chains; "
            "send `chain` alone",
            {"chain": repr(data["chain"]), "chain_template": repr(data["chain_template"])},
        )
    return data


class _OneChain(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _one_chain(cls, data: object) -> object:
        return _one_chain(data)


class NewWorkItem(_OneChain):
    title: str
    #: the brief — prose, and what the spec node writes a design from. A title is
    #: only a label.
    description: str = ""
    repo: str
    #: None means no explicit chain was chosen: the repo's `default_chain`
    #: applies, else `default` (`deps.chain_template_for`). Unchosen
    #: `default` is stored as None (Kraft-cd47), distinguishable from an item
    #: that named `chain: "default"` outright. `chain` is 2.0's name, as on
    #: every other door; `chain_template` is still read (R12D-06).
    chain_template: str | None = Field(
        default=None, validation_alias=AliasChoices("chain_template", "chain")
    )
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
    #: False (the default) creates the item without running it (design §6
    #: rule 1). An agent cannot spend tokens unattended, and neither does a
    #: raw API caller that did not ask to (Kraft-9efnk.17); the board sends
    #: it explicitly.
    autostart: bool = False
    #: Arms agent gate review for this item's `auto_escalate` gates
    #: (Kraft-zr3s). On by default; `--no-auto-gate` opts out per item.
    auto_gate: bool = True
    #: Bead ids this item implements, closed on completion. The description is
    #: no longer parsed for them: naming a bead in prose promises nothing.
    implements_beads: list[str] = []
    #: Work item ids this one comes after. Started while one is unfinished it
    #: is `blocked`, and Kraft starts it when they complete. Set here only:
    #: afterwards they can be dropped (`/unblock`), never added.
    depends_on: list[str] = []
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
                # The way on, not only the refusal (R10a-02): the file is
                # snapshotted and committed on the item's branch, so it has
                # to be one the repo holds.
                else f"attachment path escapes the repo: {a.path}. A {a.kind} must be a "
                f"file inside the repo, since it is committed on the item's branch: copy "
                f"it in, for example to .engineering/{a.kind}s/, and give that path",
            )
    return out


def _dry_run_response(
    st,
    body: NewWorkItem,
    chain: ResolvedChain,
    materialized: MaterializedChain,
    attachment_kinds: frozenset[str],
) -> dict:
    """B33: what `?dry_run=1` answers instead of filing -- the chain
    `create_work_item` would file, which nodes its attachments or
    `skip_nodes` dropped and why, the gates that will run, and each fix-loop
    node's attempts/wall clock resolved the same way `walk.walk_node` resolves
    them (`fix_loop_cap`, the node's own policy scope, its item-wide override
    and its `node_overrides` entry) -- so a node's own override and an
    item-wide value each show where they apply.

    Takes `materialized` rather than rebuilding a row: a dry run has no row to
    read a snapshot back from, and `MaterializedChain.policy_for` and
    `.item_policy` already answer exactly what a row-backed lookup would.
    """
    nodes = materialized.chain.nodes  # already trimmed by attachments/skip_nodes
    skipped = []
    covered = {n.id: n.covered_by for n in chain.nodes if n.covered_by in attachment_kinds}
    for n in chain.nodes:
        if n.id in covered:
            skipped.append({"node": n.id, "why": "covered_by", "kind": covered[n.id]})
        elif n.id in body.skip_nodes:
            skipped.append({"node": n.id, "why": "skip"})

    budget = st.policy.budget if st.policy else policy_mod.NO_BUDGET
    budget_row = {
        "budget_set": "budget_usd" in body.model_fields_set,
        "budget_usd": body.budget_usd,
    }
    budget_usd, budget_source = store.effective_work_item_cap(budget_row, budget)

    node_caps = {}
    for n in nodes:
        loop = n.node.fix_loop if isinstance(n.node, ExecNode) else None
        if loop is None:
            continue
        cap = executor.fix_loop_cap(
            st.policy,
            executor.walk._loop_key(n),
            materialized.policy_for(n),
            loop.max_attempts,
            {
                **executor.walk._item_cap(materialized.item_policy, n.id),
                **(body.node_overrides.get(n.id) or {}),
            },
        )
        node_caps[n.id] = {"attempts": cap.attempts, "wall_clock_s": cap.wall_clock_s}

    return {
        "dry_run": True,
        "nodes": [store.node_view(n) for n in nodes],
        "skipped": skipped,
        "gates": [n.id for n in nodes if isinstance(n.node, GateNode)],
        "caps": {
            "budget_usd": budget_usd,
            "budget_source": budget_source,
            "daily_usd": budget.daily_usd,
            "nodes": node_caps,
        },
    }


def repo_warning(entry) -> str | None:
    """What will stop an item filed on `entry`, said when it is filed rather
    than found once it has run: no setup command declared stops it before
    its first task, and no test command (nor test scopes) stops it at
    verification, after its agent tasks have run. Warned, not refused: a
    disabled repo still takes items filed by hand."""
    stops = []
    if entry.setup_command is None:
        stops.append(
            "declares no setup command, so this item stops before its first task, when "
            'its worktree is made (set one, or "" for none)'
        )
    if entry.test_command is None and not entry.test_scopes:
        stops.append(
            "has no test command, so this item runs its agent tasks, then stops at "
            'verification (set one, or "" for a repo with no tests)'
        )
    if not stops:
        return None
    return (
        f"{entry.path} " + "; and it ".join(stops) + ". Set it in Settings › Repos, "
        "or run `kraft repo connect` there again, before you start this item"
    )


@api_router.post("/work-items", status_code=201)
async def create_work_item(body: NewWorkItem, request: Request, dry_run: bool = False):
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
    chain_template, chain = deps.intake_chain_or_422(st, body.repo, body.chain_template)
    # Before `executor.intake`, which no longer 502s on a bd failure (Kraft-7gy)
    # and would file the item with no bead and a warning nobody reads. An
    # explicit check rather than `Field(max_length=...)`: pydantic's 422 body is
    # a list of error dicts, and `kraft item create` prints `detail` straight
    # through -- one sentence is the contract every other CLI error keeps.
    _check_title(body.title)
    _check_text("description", body.description)
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
    repo_steering = deps.repository_steering_or_422(st, body.repo, target)
    try:
        materialized = chain.materialize(
            target=target,
            effective_policy=policy,
            repository_policies=per_repository,
            attachment_kinds=attachment_kinds,
            skip_nodes=frozenset(body.skip_nodes),
        ).with_item_policy(body.policy)
        item_policy = materialized.item_policy
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _check_node_overrides(
        body.node_overrides,
        node_ids,
        {n.id: n for n in chain.nodes},
        deps.instance_policy(st).maxima,
    )
    if dry_run:
        # B33: every check up to and including `_check_node_overrides` has run;
        # nothing is written -- no row, no bead, no attachment copy, no spawn.
        # 200, not this route's default 201: nothing was created.
        content = _dry_run_response(st, body, chain, materialized, attachment_kinds)
        return JSONResponse(status_code=200, content=content)
    # Before intake: a lookup after it could answer 422 for an item filed.
    repo_entry = deps.connected_or_422(st, body.repo)
    # Read before this item exists, so it cannot find itself. Warned, not
    # refused (Kraft-s7c04.30): a deliberate second item is legitimate, and
    # the usual reason to re-file, a revised spec, now has its own door.
    depends_on = list(dict.fromkeys(body.depends_on))
    if problem := st.db.read(lambda c: store.dependency_problem(c, depends_on)):
        raise HTTPException(422, problem)
    # An autostart behind an unfinished item is filed and then blocked: it
    # must not take a slot, so it is not handed to `intake` as active.
    waits = st.db.read(lambda c: store.any_unfinished(c, depends_on))
    start_now = body.autostart and not waits
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
            # `chain_template_for`'s value, not the resolved template's id
            # (Kraft-cd47): None here means nothing chose a template, and must
            # stay None in the row -- `intake`'s own default would otherwise
            # store `template.id`, indistinguishable from an item that named
            # `chain_template: "default"` outright.
            chain_template=chain_template,
            bd_cwd=deps.bd_cwd(),
            target=target,
            repository_policies=per_repository,
            repository_steering=repo_steering,
            attachments=attachments,
            status=WorkItemStatus.ACTIVE if start_now else WorkItemStatus.PAUSED,
            # Folded into intake's own INSERT transaction, not a separate
            # `active_count` read here: two autostart creates racing a few
            # milliseconds apart must not both see a free slot and both win
            # one (Kraft-m43g, Kraft-nxht). `None` when not autostarting --
            # `status` is already "paused" and needs no capacity decision.
            limit=(
                0
                if storage.state_of(st.policy, storage.usage(st)) == "held"
                else (st.policy.max_concurrent if st.policy else 1)
            )
            if start_now
            else None,
            auto_gate=body.auto_gate,
            implements_beads=body.implements_beads,
            skip_nodes=frozenset(body.skip_nodes),
            budget_set="budget_usd" in body.model_fields_set,
            budget_usd=body.budget_usd,
            # A `null` field drops it on a PATCH; at intake there is nothing to drop.
            node_overrides={
                n: kept
                for n, f in body.node_overrides.items()
                if (kept := {k: v for k, v in f.items() if v is not None})
            }
            or None,
            policy_override=item_policy.model_dump(exclude_none=True, exclude_defaults=True)
            if item_policy is not None
            else None,
        )
    except Exception as exc:  # noqa: BLE001 -- executor.intake raises several unrelated types
        # No longer reachable for a bd failure — `executor.intake` degrades
        # instead (Kraft-7gy). A 502 here is now a template or a DB problem.
        raise HTTPException(502, f"intake failed: {exc}") from exc

    if depends_on:
        await st.db.write(lambda c: store.set_dependencies(c, wid, depends_on))

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
    if cannot_run := repo_warning(repo_entry):
        extra["repo_warning"] = cannot_run

    if body.autostart and waits:
        await st.db.write(
            lambda c: store.block_work_item(
                c,
                wid,
                verb="resume",
                body={},
                headers=deps.caller_headers(request),
                from_statuses=[WorkItemStatus.PAUSED],
            )
        )
        waiting_on = st.db.read(lambda c: store.unmet_dependencies(c, wid))
        return {"id": wid, "status": WorkItemStatus.BLOCKED, "waiting_on": waiting_on, **extra}

    if not body.autostart:
        # Created, not started. `/resume` begins it at node zero, because a NULL
        # current_node_id falls through that handler's `next(..., 0)` default.
        return {"id": wid, "status": WorkItemStatus.PAUSED, **extra}

    row = st.db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    if row["status"] not in HOLDS_SLOT:
        # Lost the capacity race inside `intake`'s own INSERT (Kraft-m43g,
        # Kraft-nxht): filed, and queued for the slot it asked for. `slots`
        # says why it is not running yet.
        await st.db.write(
            lambda c: store.queue_work_item(
                c,
                wid,
                verb="resume",
                body={},
                headers=deps.caller_headers(request),
                from_statuses=[WorkItemStatus.PAUSED],
            )
        )
        slots = await lifecycle.slots_answer(st, wid)
        return {"id": wid, "status": WorkItemStatus.QUEUED, "slots": slots, **extra}

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


def _duplicate_target(st, row):
    """The source item's `WorkItemTarget` selection, rebuilt through
    `deps.workspace_target` the way a create's own `NewWorkItem.workspace/
    members/root_pointer_policy` would -- "resolved again", like B3's
    `chain_template`, not read back verbatim from a frozen snapshot. `None`
    for a single-repository item (`store.repos_for` is empty for one).

    ponytail: the row has no column naming which declared workspace was
    picked, only its root and member *paths* (`store.repos_for`,
    `root_merge_policy`); this matches them back to a workspace declared
    with that root, first one found. Good enough while `repos.yaml` holds at
    most one workspace per root -- store the workspace id on intake instead
    if that ever stops being true.
    """
    repo_rows = st.db.read(lambda c: store.repos_for(c, row["id"]))
    if not repo_rows:
        return None
    root_path = next(r["path"] for r in repo_rows if r["role"] == "root")
    member_paths = {r["path"] for r in repo_rows if r["role"] != "root"}
    try:
        repos = config_mod.load_repos(deps.repos_path(st))
        workspaces = config_mod.load_workspaces(deps.repos_path(st))
    except config_mod.ConfigError as exc:
        raise HTTPException(422, str(exc)) from exc
    by_path = {r.path: r for r in repos}
    root_entry = by_path.get(root_path)
    if root_entry is None or root_entry.id is None:
        raise HTTPException(422, f"{root_path} is no longer a declared repository")
    member_ids = [by_path[p].id for p in member_paths if p in by_path and by_path[p].id]
    ws_id = next((wid for wid, ws in workspaces.items() if ws.root == root_entry.id), None)
    if ws_id is None:
        raise HTTPException(422, f"no workspace is declared with root {root_path}")
    pointer_policy = (
        RootPointerPolicy(row["root_merge_policy"]) if row["root_merge_policy"] else None
    )
    return deps.workspace_target(
        st, row["repo"], workspace=ws_id, members=member_ids, root_pointer_policy=pointer_policy
    )


def _duplicate_attachments(row) -> list[dict]:
    """The source's stored attachment copies, re-pointed as the new item's own
    intake input: `source` keeps naming the existing file under
    `run/attachments/<source id>/...` so `executor.intake`'s own
    `_store_attachments` copies *that*, under the new item's id, rather than
    re-reading `path` relative to the repo (which is the worktree
    destination, not where Kraft keeps its copy). Archive keeps the copies;
    abandon deletes them. A copy that has gone missing answers 409 naming it,
    ahead of `executor.intake` -- which would otherwise fold the same
    `OSError` into its own blanket 502."""
    out = []
    for a in entry.attachments_of(row):
        src = a.get("source")
        if not src or not Path(src).is_file():
            raise HTTPException(
                409,
                f"cannot duplicate: Kraft no longer has this item's {a['kind']} "
                f"({src or a['path']}). Abandoning an item deletes its attachments, and "
                "so did archiving one on a 1.5.0 release candidate. "
                f"File a new item and attach the {a['kind']} again.",
            )
        out.append({"kind": a["kind"], "path": a["path"], "source": src})
    return out


# B3.
@api_router.post("/work-items/{wid}/duplicate", status_code=201)
async def duplicate_work_item(wid: str, request: Request):
    """A fresh, paused item from `wid`'s own title, description, repo,
    chain template, workspace selection and attachments -- any source status
    accepted, archived and cancelled included. No run state, override,
    policy override, budget or bead link carries over; this is a fresh
    intake, not a clone of the row.

    An agent may call this the same way it may `POST /work-items`: the new
    item always files paused, so there is nothing here for `autostart`'s
    human-only rule to guard, and nothing of `wid`'s own run is touched --
    `deps.forbid_self_action` is for an action *on* a live item, which this
    is not.
    """
    st = request.app.state
    if st.invalid_policy:
        detail = "; ".join(st.invalid_policy)
        raise HTTPException(503, f"policy config invalid, refusing work: {detail}")
    row = deps._work_item_row(st, wid)
    chain_template, chain = deps.intake_chain_or_422(st, row["repo"], row["chain_template"])
    attachments = _duplicate_attachments(row)
    attachment_kinds = frozenset(a["kind"] for a in attachments)
    # `materialize`/`intake` always take a real target, the same as `POST
    # /work-items` falling back to a plain `single_repo_target` when nothing
    # selected a workspace (`_duplicate_target` returns `None` for exactly
    # that case, `workspace_target`'s own convention).
    target = _duplicate_target(st, row) or entry.single_repo_target(row["repo"])
    policy = deps.item_policy_or_422(st, row["repo"], target)
    per_repository = deps.repository_policies_or_422(st, target)
    repo_steering = deps.repository_steering_or_422(st, row["repo"], target)
    try:
        item_policy = chain.materialize(
            target=target,
            effective_policy=policy,
            repository_policies=per_repository,
            attachment_kinds=attachment_kinds,
        ).item_policy
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    # A title 1.4 stored on several lines, or blank, is folded as a cron
    # trigger's is (`entry.one_line_title`); one with no text anywhere is refused.
    title, description = entry.one_line_title(row["title"], row["description"] or "")
    if not title.strip():
        raise HTTPException(422, "title cannot be empty: give the item a title first")
    duplicates = st.db.read(lambda c: store.open_duplicates(c, row["repo"], title, []))
    try:
        new_id = await executor.intake(
            st.db,
            st.run_dirs,
            title=title,
            description=description,
            repo=row["repo"],
            chain=chain,
            effective_policy=policy,
            chain_template=chain_template,
            bd_cwd=deps.bd_cwd(),
            target=target,
            repository_policies=per_repository,
            repository_steering=repo_steering,
            attachments=attachments,
            status=WorkItemStatus.PAUSED,
            auto_gate=True,
            policy_override=item_policy.model_dump(exclude_none=True, exclude_defaults=True)
            if item_policy is not None
            else None,
        )
    except Exception as exc:  # noqa: BLE001 -- executor.intake raises several unrelated types
        raise HTTPException(502, f"intake failed: {exc}") from exc
    extra = {}
    if duplicates:
        extra["duplicate_warning"] = (
            "looks like open work item "
            + "; ".join(f"{r['id']} ({r['status']}, {why})" for r, why in duplicates)
            + ". To revise its spec or plan, use `kraft item set-attachments` on it "
            "rather than filing again; abandon whichever of the two is not wanted"
        )
    return {"id": new_id, "status": WorkItemStatus.PAUSED, **extra}


def _check_title(title: str) -> None:
    """The title checks both intake doors make, each a one-sentence 422. A
    blank title is refused the way a `PATCH` refuses one: the board would
    show the item as a bare dash."""
    if not title.strip():
        raise HTTPException(422, "title cannot be empty")
    _check_one_line(title)
    if len(title) > beads_mod.MAX_TITLE:
        raise HTTPException(
            422,
            f"title is {len(title)} characters; the tracker's limit is {beads_mod.MAX_TITLE}",
        )


#: Every character `str.splitlines` breaks a line at.
_LINE_BREAKS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")


def _check_one_line(title: str) -> None:
    """A title is one line: a line break in it broke `kraft view list`'s
    table, the row wrapping under the ID column. A trailing one too, as the
    blank-title refusal refuses rather than strips."""
    if not _LINE_BREAKS.isdisjoint(title):
        raise HTTPException(422, "the title is one line: put the rest in the description instead")
    # A tab misaligned the same table; an escape or a bidi override in a title
    # `kraft view list` prints raw can make a terminal show something else. A
    # lone surrogate (`\ud800`, which JSON can spell) is no character at all:
    # it failed the write with a 500 (R12s-01).
    if any(unicodedata.category(ch) in ("Cc", "Cs") or ch in _BIDI_CONTROLS for ch in title):
        raise HTTPException(
            422, "the title is plain text: no tab, escape or other control character"
        )


def _check_text(name: str, text: str | None) -> None:
    """A lone surrogate is no text: storing one failed the write (500/502)."""
    if text is not None and any(unicodedata.category(ch) == "Cs" for ch in text):
        raise HTTPException(422, f"the {name} is not text: it holds a lone surrogate")


#: The characters that reorder text as it is shown (Unicode's bidi controls).
#: Not the marks (ALM, LRM, RLM): they are common in pasted Persian, Arabic
#: and Hebrew text, and cannot reorder strong text.
_BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


class TriggerBody(_OneChain):
    repo: str
    title: str
    description: str = ""
    #: `chain_template` before 2.0; both names are read.
    chain: str | None = Field(
        default=None, validation_alias=AliasChoices("chain", "chain_template")
    )


# Kraft-859.
@api_router.post("/triggers", status_code=201)
async def fire_trigger(body: TriggerBody, request: Request):
    """The HTTP twin of a policy.yaml cron trigger -- always
    paused, for the same reason: an agent cannot start work here any more
    than it can from manual intake or a cron tick."""
    st = request.app.state
    if st.invalid_policy:
        detail = "; ".join(st.invalid_policy)
        raise HTTPException(503, f"policy config invalid, refusing work: {detail}")
    chain_template, chain = deps.intake_chain_or_422(st, body.repo, body.chain)
    _check_title(body.title)
    _check_text("description", body.description)
    if not Path(body.repo).is_dir():
        raise HTTPException(422, f"repo path does not exist: {body.repo}")
    policy = deps.item_policy_or_422(st, body.repo)
    repo_steering = deps.repository_steering_or_422(st, body.repo)
    try:
        wid = await executor.intake(
            st.db,
            st.run_dirs,
            title=body.title,
            description=body.description,
            repo=body.repo,
            chain=chain,
            effective_policy=policy,
            repository_steering=repo_steering,
            chain_template=chain_template,
            bd_cwd=deps.bd_cwd(),
            status=WorkItemStatus.PAUSED,
        )
    except Exception as exc:  # noqa: BLE001 -- executor.intake raises several unrelated types
        raise HTTPException(502, f"intake failed: {exc}") from exc
    # Always paused, so this always takes create_work_item's "not autostart"
    # branch — no _spawn, no executor.run. bead_warning is dropped: a trigger
    # has no CLI kv block reading it, and get_work_item below already
    # surfaces the item's real state.
    return await board.get_work_item(wid, request)


class WorkItemPatch(_OneChain):
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
    #: started item. `chain`, 2.0's name, is read too.
    chain_template: str | None = Field(
        default=None, validation_alias=AliasChoices("chain_template", "chain")
    )
    #: `None` (default) leaves the override alone. `{}` clears every field
    #: back to the template's own binding; a non-empty object *replaces* the
    #: whole stored override, as in 1.4 -- a field it does not name, or sends
    #: as `null`, is dropped. The item page sends every field in one PATCH.
    #: No `current_node_id` restriction, unlike `chain_template`:
    #: a model/effort dial can change mid-chain, including on a paused item --
    #: that is the point, making a stuck item cheaper before its next retry.
    agent_overrides: dict | None = None
    #: Per-node field overrides (UI v2 · 04 point 1). `None` (default) leaves
    #: overrides alone. `{}` resets every node to the template -- refused
    #: (409) once the item has started. A non-empty object is per node id:
    #: `{node_id: {}}` drops that node's overrides, `{node_id: {field:
    #: value}}` sets fields on it and `{node_id: {field: null}}` drops one --
    #: merged into what's already stored, so toggling one node's switch never
    #: wipes another's. Refused (409) for any node id that has already started.
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
    nodes: dict | None,
    maxima: PolicyMaximaInput | None = None,
) -> None:
    """422 on an override naming no node of the chain, an unknown field, a
    model/effort a node's harness refuses (`overrides.harness_refusal`), or an
    `auto_escalate: true` on a gate declaring no `auto_review`. One check for
    intake and `PATCH`, so the two doors cannot answer differently (review E
    #3). `nodes` maps node id to its `ResolvedNode`; `None` (a legacy row, with
    no typed chain) skips the last two checks.

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
        if nodes is not None and (why := harness_refusal(nodes[node_id], fields)):
            raise HTTPException(422, f"node {node_id!r}: {why}")
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
            nodes is not None
            and fields.get("auto_escalate") is True
            and nodes[node_id].auto_review is None
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
    # and never raises; only `nodes` needs the typed snapshot, because
    # `auto_review` and a task's harness have no legacy equivalent.
    node_ids = set(store.chain_node_ids(row))
    v1 = store.materialized_chain_of(row)
    nodes = {n.id: n for n in v1.chain.nodes} if v1 is not None else None
    if not patch:
        if row["current_node_id"] is not None:
            raise HTTPException(409, "work item has already started; overrides cannot be reset")
        return

    _check_node_overrides(patch, node_ids, nodes, deps.instance_policy(st).maxima)

    def check(c):
        for node_id, fields in patch.items():
            if store.node_started(c, row["id"], node_id) and not _repairs_refused_model(
                row, node_id, fields
            ):
                raise HTTPException(409, f"node {node_id!r} has started; its config is locked")

    st.db.read(lambda c: check(c))


#: What a repair of a refused stored model may touch on a started node.
_MODEL_FIELDS = frozenset({"model", "escalate_model", "effort"})


def _repairs_refused_model(row, node_id: str, fields: dict) -> bool:
    """Whether `fields` repairs a node the item is stopped at because its
    stored model override is no model id (`overrides.stored_model_refusal`,
    the overrides 1.4 stored as any text): a clear (`{}`), or a change to its
    model fields alone. The node started before the launch was refused, so
    its lock answered 409 to the very command the stop names, and a retry
    stopped again. No agent of that node ever ran with that model, so there
    is nothing the change could misreport."""
    if row["status"] != WorkItemStatus.NEEDS_HUMAN or row["current_node_id"] != node_id:
        return False
    if not set(fields) <= _MODEL_FIELDS:
        return False
    stored = store.node_overrides_of(row).get(node_id) or {}
    return any(model_id_problem(stored.get(k)) is not None for k in ("model", "escalate_model"))


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
        raise HTTPException(409, "this work item has no chain snapshot to hold a policy override")
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
        raise HTTPException(409, "this work item has no chain snapshot to re-trim")
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
        ResolvedChain.from_chain(
            untrimmed, steering=previous.chain.steering, plugins=previous.chain.plugins
        )
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
    # The fields `kraft` guards with `_forbid_self_action`, plus the budget: a
    # worker raising its own dollar cap is the same self-action. Title and
    # description stay open; a chain is fixed once the item starts.
    # The budget and the policy (which can raise `budget_usd` and the time
    # caps item-wide) are a person's call, so an escalation turn is refused
    # them too, like a gate (Kraft-9efnk.29).
    spending = "budget_usd" in body.model_fields_set or body.policy is not None
    if spending or any(
        f is not None for f in (body.attachments, body.agent_overrides, body.node_overrides)
    ):
        deps.forbid_self_action(st, request, wid, escalation_may=not spending)
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
            "nothing to patch: send a title, description, chain, agent_overrides, "
            "node_overrides, policy, attachments, or budget_usd",
        )
    if body.title is not None and not body.title.strip():
        raise HTTPException(422, "title cannot be empty")
    if body.title is not None:
        _check_one_line(body.title)
    _check_text("description", body.description)

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
            raise HTTPException(404, f"unknown chain {body.chain_template!r}")
        if row["current_node_id"] is not None:
            raise HTTPException(
                409, "work item has already started; its chain is fixed for its life"
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
                    # Not the chain's: the repositories' steering the item was
                    # filed with stays frozen across a template switch.
                    repository_steering=previous.repository_steering if previous else None,
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
        v1 = store.materialized_chain_of(row)
        if v1 is not None:
            nodes = {n.id: n for n in v1.chain.nodes}
            if (why := item_harness_refusal(nodes, body.agent_overrides)) is not None:
                raise HTTPException(422, why)

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
            written["agent_overrides"] = store.replace_agent_overrides(c, wid, body.agent_overrides)
        if body.node_overrides is not None:
            now = store.set_node_overrides(c, wid, body.node_overrides)
            # Each node the request named, as stored: `{node: {}}` once it is
            # cleared, as 1.4 echoed it, and never another node's entry.
            written["node_overrides"] = {n: now.get(n, {}) for n in body.node_overrides}
        if "budget_usd" in fields_set:
            store.set_budget(c, wid, body.budget_usd)
        if body.policy is not None:
            store.set_policy_override(c, wid, item_policy)

    # The overrides as stored after the write, which the echo reports in
    # place of what was sent: `{}` once cleared, and for a node it named a
    # field the request left out is still there.
    written: dict = {}
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
    # `fields_set` (what the caller actually sent) instead. For the overrides
    # the echo is what is now stored, as the detail shows it.
    return {"id": wid, **{f: getattr(body, f) for f in fields_set}, **written}
