from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from pathlib import Path

from kraft import events, store
from kraft.adapters import beads
from kraft.templates import ATTACHMENT_GATES, Template, materialize

logger = logging.getLogger(__name__)

#: `intake`'s `chain_template` default (Kraft-cd47). Distinct from `None`,
#: which a caller passes to mean "no explicit template was chosen" and store
#: as such -- `_UNSET` means the caller (most of them, today: everything but
#: the `/work-items` endpoint) does not care and gets the old behaviour,
#: `template.id`, so a chain built straight from a `Template` still records
#: which one without every internal caller having to say so.
_UNSET = object()


async def intake(
    db,
    run_dirs,
    *,
    title: str,
    repo: str,
    template: Template,
    description: str | None = None,
    bd_cwd: str | None = None,
    submodules: list[str] | None = None,
    root_merge_policy: str = "bump",
    attachments: list[dict] | None = None,
    status: str = "active",
    bead_id: str | None = None,
    bead_cwd: str | None = None,
    #: The value to store in the row's `chain_template` column. `_UNSET`
    #: (default) stores `template.id`; `None` stores `None` -- the caller
    #: that wants that distinction (Kraft-cd47) has to say so explicitly.
    chain_template: str | None | object = _UNSET,
    #: Arms agent gate review for this item's `auto_escalate` gates
    #: (Kraft-zr3s). Off at this layer even though the create doors default it
    #: on: `intake` is also the auto-start path, and an item nobody asked for
    #: must pass no gate automatically. The caller that has a human behind it
    #: passes the value.
    auto_gate: bool = False,
    #: Node ids to remove from the materialized chain at intake (UI v2 · 04
    #: point 6, `templates.materialize`'s `skip_nodes`). Already validated
    #: against the template by the caller.
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
    satisfied = frozenset(ATTACHMENT_GATES[a["kind"]] for a in attachments or [])
    chain_definition = json.dumps(
        materialize(template, satisfied_gates=satisfied, skip_nodes=skip_nodes)
    )
    implements_beads = _extract_beads(description, exclude=bead_id)

    def _create(c):
        store.create_work_item(
            c,
            id=work_item_id,
            bead_id=bead_id,
            title=title,
            description=description,
            repo=repo,
            chain_template=template.id if chain_template is _UNSET else chain_template,
            chain_definition=chain_definition,
            # Recorded on every new row, so a bead is closed where it was filed
            # whatever KRAFT_BD_CWD says months later.
            bead_cwd=bead_cwd or cwd,
            submodules=submodules,
            root_merge_policy=root_merge_policy,
            attachments=attachments,
            status=status,
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


#: A sub-bead id as it appears in a work item's description, e.g. `Kraft-p8q1`.
_BEAD_ID_RE = re.compile(r"Kraft-[a-z0-9]+")


def _extract_beads(description: str | None, *, exclude: str | None = None) -> list[str]:
    """Sub-bead ids named in `description` (Kraft-p8q1), deduped, order preserved.

    Free data: every item on the board already writes its beads as
    `- Kraft-xxxx — ...` bullets. `exclude` drops the tracking bead itself, in
    case it happens to be quoted back in its own description.
    """
    seen: list[str] = []
    for match in _BEAD_ID_RE.findall(description or ""):
        if match != exclude and match not in seen:
            seen.append(match)
    return seen


def _implements_beads(work_item_row) -> list[str]:
    """The row's sub-bead ids, tolerating a row that predates the column."""
    if "implements_beads" not in work_item_row.keys():
        return []
    raw = work_item_row["implements_beads"]
    return json.loads(raw) if raw else []


async def close_beads(db, row, bd_cwd: str | None) -> None:
    """Close `row['bead_id']` plus every id in `row['implements_beads']`.

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
    for sub_id in _implements_beads(row):
        try:
            await beads.complete(sub_id, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            logger.warning("bead close failed for %s: %r", sub_id, exc)
