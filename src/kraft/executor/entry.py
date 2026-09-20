from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from pathlib import Path

from kraft import events, store
from kraft.adapters import beads
from kraft.config import git_read
from kraft.policy import InstancePolicy, InstancePolicyInput
from kraft.templates.environment import Repository, WorkItemTarget
from kraft.templates.models import ResolvedChain

logger = logging.getLogger(__name__)

#: `intake`'s `chain_template` default (Kraft-cd47). Distinct from `None`,
#: which a caller passes to mean "no explicit template was chosen" and store
#: as such -- `_UNSET` means the caller (most of them, today: everything but
#: the `/work-items` endpoint) does not care and gets the old behaviour,
#: `template.id`, so a chain built straight from a `Template` still records
#: which one without every internal caller having to say so.
_UNSET = object()


def single_repo_target(repo: str) -> WorkItemTarget:
    """The immutable target of an item filed against one repository.

    The id is the constant `"target"`, not the repository's own configured id:
    a single-repository item has exactly one target, nothing reads the name,
    and a `repos.yaml` id is not available at every intake door. A *workspace*
    target carries real member ids and is Phase 6's.

    Shared with the `/work-items` route, which dry-runs the same
    materialization before it files a bead -- two copies of this would let the
    dry run and the real one disagree about what gets trimmed.
    """
    return WorkItemTarget.for_repository(Repository(id="target", path=repo))


async def intake(
    db,
    run_dirs,
    *,
    title: str,
    repo: str,
    #: The resolved V1 chain this item runs. Materialized here, into the
    #: `materialized_chain` column, which is the executor's only input
    #: (`materialized-chain-is-immutable-work-item-input`).
    chain: ResolvedChain,
    #: The V1 instance policy this item's tasks start from; the chain's own
    #: `policy:` override is layered onto it by `materialize`. Defaults to the
    #: empty policy -- an unset maximum is no bound at all -- so an internal
    #: caller with no policy object still files a valid item.
    effective_policy: InstancePolicy | None = None,
    description: str | None = None,
    bd_cwd: str | None = None,
    submodules: list[str] | None = None,
    root_merge_policy: str = "bump",
    attachments: list[dict] | None = None,
    status: str = "active",
    #: When given, `status="active"` is downgraded to `"paused"` if
    #: `active_count()` is already at `limit` -- read on the same connection
    #: the INSERT below runs on, inside the same `db.write` transaction, so
    #: the check and the flip are one statement's worth of atomicity rather
    #: than a read a concurrent create/resume/retry can race (Kraft-m43g,
    #: Kraft-nxht). `None` (the default, and every caller but the `/work-items`
    #: autostart path) means "no cap enforced here" -- unchanged behaviour.
    limit: int | None = None,
    bead_id: str | None = None,
    #: Bead ids this item implements and should close on completion. Explicit
    #: on purpose: this was scraped out of `description` with a regex, so a
    #: bead mentioned as context -- or in a sentence saying it was NOT in scope
    #: -- was closed anyway, four times. There is no fallback to parsing prose;
    #: a fallback is the trap.
    implements_beads: list[str] | None = None,
    bead_cwd: str | None = None,
    #: The value to store in the row's `chain_template` column. `_UNSET`
    #: (default) stores `chain.id`; `None` stores `None` -- the caller
    #: that wants that distinction (Kraft-cd47) has to say so explicitly.
    chain_template: str | None | object = _UNSET,
    #: Arms agent gate review for this item's `auto_escalate` gates
    #: (Kraft-zr3s). Off at this layer even though the create doors default it
    #: on: `intake` is also the auto-start path, and an item nobody asked for
    #: must pass no gate automatically. The caller that has a human behind it
    #: passes the value.
    auto_gate: bool = False,
    #: Node ids to remove from the materialized chain at intake (UI v2 · 04
    #: point 6, `ResolvedChain.materialize`'s `skip_nodes`). Already validated
    #: against the chain by the caller.
    skip_nodes: frozenset[str] = frozenset(),
    #: Intake-time spend cap and per-node overrides, forwarded verbatim to
    #: `store.create_work_item` (point 6). `budget_set=False` (default)
    #: means "no explicit cap at intake, policy default applies".
    budget_set: bool = False,
    budget_usd: float | None = None,
    node_overrides: dict[str, dict] | None = None,
    source: str | None = None,
    bead_priority: int | None = None,
) -> str:
    work_item_id = uuid.uuid4().hex
    # A daemon's cwd is an accident of how it was launched — launchd, a login
    # item, `kraft admin start` typed in $HOME — and nothing records it, so the work
    # item's own repo is the default bd workspace (Kraft-ibwj). KRAFT_BD_CWD
    # still wins: it is documented as the instance-wide tracker, `just dev` and
    # the e2e harness set it, and an operator who set it as the workaround for
    # this very bug must not silently start filing per-repo on upgrade.
    cwd = bd_cwd or repo
    # An auto-intaken bead already exists; filing a second one for the same work
    # is the duplicate this parameter prevents.
    bead_warning: str | None = None
    if bead_id is None:
        try:
            bead_id = await beads.intake(title, description=description, cwd=cwd)
        except OSError as exc:
            # bd is not on PATH, or `cwd` no longer exists.
            bead_warning = f"bd is not installed: {exc}"
        except Exception as exc:  # noqa: BLE001 -- any bd failure degrades
            # Deliberately "any bd failure", not "the two we can name":
            # separating "no workspace" from "dolt is wedged" means
            # string-matching bd's stderr, and the bead is a tracking
            # side-effect while Kraft's own DB runs the chain (Kraft-7gy).
            # `beads.intake` already puts bd's own words in this message.
            bead_warning = str(exc)
        if bead_warning:
            logger.warning("bead not filed for %r in %s: %s", title, cwd, bead_warning)
    # Before the trim below, and raising rather than degrading: `materialize`
    # is about to remove this attachment's gate from the chain permanently, and
    # a trim whose document is not Kraft's own is a promise something outside
    # Kraft can later make false (Kraft-eqgn). Intake is the last moment the
    # caller can fix the path, so it is where this fails.
    attachments = _store_attachments(run_dirs, work_item_id, attachments, repo=repo)
    # An attachment's trim is the V1 chain's own decision -- the gate's
    # `artifact:` and the producing node's `produces:`, never a kind-to-gate-
    # name table this layer looks up -- and it happens in the same drop as
    # `skip_nodes`, so a chain the two together would empty is refused once.
    materialized = chain.materialize(
        target=single_repo_target(repo),
        effective_policy=(
            effective_policy
            if effective_policy is not None
            else InstancePolicy.from_input(InstancePolicyInput())
        ),
        # Derived from the attachments themselves, never passed in beside
        # them: the kinds that trim the chain and the documents that justify
        # the trim have to be the same list, and a caller holding both is a
        # caller that can make them disagree.
        attachment_kinds=frozenset(a["kind"] for a in attachments),
        skip_nodes=skip_nodes,
    )
    implements_beads = [b for b in (implements_beads or []) if b != bead_id] or None

    def _create(c):
        effective_status = status
        if limit is not None and status == "active" and store.active_count(c) >= limit:
            effective_status = "paused"
        store.create_work_item(
            c,
            id=work_item_id,
            bead_id=bead_id,
            title=title,
            description=description,
            repo=repo,
            chain_template=chain.id if chain_template is _UNSET else chain_template,
            # `"{}"`, not the legacy envelope: the column is NOT NULL and Task
            # 11 removes it. Nothing in a V1 walk reads it -- `walk.chain_of`
            # reads `materialized_chain` and has no fallback.
            chain_definition="{}",
            materialized_chain=materialized.to_json(),
            # Recorded on every new row, so a bead is closed where it was filed
            # whatever KRAFT_BD_CWD says months later.
            bead_cwd=bead_cwd or cwd,
            submodules=submodules,
            root_merge_policy=root_merge_policy,
            attachments=attachments,
            status=effective_status,
            implements_beads=implements_beads,
            auto_gate=auto_gate,
            budget_set=budget_set,
            budget_usd=budget_usd,
            node_overrides=node_overrides,
            source=source,
            bead_priority=bead_priority,
        )
        if bead_warning:
            # Same transaction as the row: an item with no bead and no record of
            # why is the silent swallow this degrade is not.
            events.append(c, work_item_id, "bead_not_filed", {"reason": bead_warning, "cwd": cwd})

    await db.write(_create)
    return work_item_id


def _store_attachments(
    run_dirs, work_item_id: str, attachments: list[dict] | None, *, repo: str
) -> list[dict]:
    """Copy each attachment into Kraft's own storage; return the rewritten records.

    `path` is left alone — it is the destination inside the worktree, and
    `ensure_worktree` still needs it. Only `source` changes, from "where the
    caller had it" to "where Kraft keeps it", which is why `ensure_worktree`
    needs no change at all: it already prefers `source`.

    Raises rather than skipping. Every other reader of an attachment is
    best-effort, and that is right for them; this one backs an irreversible
    decision.
    """
    if not attachments:
        return attachments or []
    dest_dir = run_dirs.attachments / work_item_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    stored = []
    for a in attachments:
        src = Path(a["source"]) if a.get("source") else Path(repo) / a["path"]
        dest = dest_dir / f"{a['kind']}{Path(a['path']).suffix or '.md'}"
        # Not shutil.copyfile's own error message: it names two absolute paths
        # under $KRAFT_HOME and says nothing about which attachment this was.
        try:
            shutil.copyfile(src, dest)
        except OSError as exc:
            raise ValueError(f"cannot read the {a['kind']} attachment at {src}: {exc}") from exc
        stored.append({**a, "source": str(dest)})
    return stored


def attachments_of(work_item_row) -> list[dict]:
    """The row's intake attachments, tolerating a row that predates the column."""
    if "attachments" not in work_item_row.keys():
        return []
    raw = work_item_row["attachments"]
    return json.loads(raw) if raw else []


#: `Fixes Kraft-abc12` / `Closes: Kraft-abc12` in a commit message. Unlike a
#: bare id anywhere in prose, this is a promise the author made deliberately,
#: and it is the convention every forge already reads. It is also the signal
#: that tracks implementation: a bead fixed by a merged commit stayed open for
#: a day because nothing here read it.
_TRAILER_RE = re.compile(r"\b(?:Fixes|Closes)\b:?\s+(Kraft-[a-z0-9]+(?:\.[0-9]+)*)", re.I)


def _trailer_beads(messages: list[str]) -> list[str]:
    """Bead ids promised by `Fixes`/`Closes` trailers, deduped, order preserved."""
    seen: list[str] = []
    for message in messages:
        for match in _TRAILER_RE.findall(message):
            if match not in seen:
                seen.append(match)
    return seen


def _implements_beads(work_item_row) -> list[str]:
    """The row's sub-bead ids, tolerating a row that predates the column."""
    if "implements_beads" not in work_item_row.keys():
        return []
    raw = work_item_row["implements_beads"]
    return json.loads(raw) if raw else []


async def close_beads(db, row, bd_cwd: str | None, run_dirs) -> None:
    """Close `row['bead_id']`, every id in `row['implements_beads']`, and every id
    a `Fixes`/`Closes` trailer on the item's own commits names.

    An item filed while bd was down has no `bead_id` (Kraft-7gy); this backfills
    one via a late `beads.intake` before closing, rather than leaving it open
    forever (Kraft-dr3n) -- and persists it to the row, same as if intake had
    filed it originally. Each id's failure is logged, not raised -- one bad bead
    must not stop the others from closing, same as the single-bead path before.
    """
    cwd = row["bead_cwd"] or bd_cwd
    bead_id = row["bead_id"]
    if bead_id is None:
        try:
            bead_id = await beads.intake(row["title"], description=row["description"], cwd=cwd)
        except Exception as exc:  # noqa: BLE001 -- any bd failure degrades, same as intake
            logger.warning("late bead intake failed for %r: %r", row["title"], exc)
        else:
            await db.write(lambda c: store.set_bead_id(c, row["id"], bead_id))
    if bead_id:
        try:
            await beads.complete(bead_id, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            logger.warning("bead close failed for %s: %r", bead_id, exc)
    # `git_read` never raises, so a worktree already cleaned up, or a row with no
    # `base_ref`, degrades to closing exactly the stated beads: a bead left open
    # is found, a bead wrongly closed is not.
    worktree = run_dirs.worktrees / row["id"]
    log = (
        git_read(worktree, "log", "--format=%B%x00", f"{row['base_ref']}..HEAD")
        if row["base_ref"]
        else None
    )
    trailers = _trailer_beads(log.split("\0") if log else [])
    stated = _implements_beads(row)
    for sub_id in [*stated, *(x for x in trailers if x != bead_id and x not in stated)]:
        try:
            await beads.complete(sub_id, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            logger.warning("bead close failed for %s: %r", sub_id, exc)
