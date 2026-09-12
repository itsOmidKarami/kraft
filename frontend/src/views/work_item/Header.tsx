import { useEffect, useState, type WheelEvent } from "react";
import { CaretDown, PencilSimple, Prohibit } from "@phosphor-icons/react";
import * as api from "../../api";
import { elapsed, repoName, statusWord } from "../../format";
import { useStore } from "../../store";
import { OverflowMenu, TaskBar, TaskLine } from "../../components/ui";
import type { KraftEvent, WorkItem } from "../../types";

/**
 * Desktop 11's header (UI v2 · 05): the meta line, the title/description
 * editors carried over from the old `WorkItemDetail.tsx` unchanged, and the
 * hero node block on the right.
 */

const STATUS_TAG: Record<WorkItem["status"], string> = {
  active: "tag tag-outline",
  completed: "tag tag-neutral",
  needs_human: "tag tag-accent",
  paused: "tag tag-neutral",
  abandoned: "tag tag-neutral",
  rate_limited: "tag tag-outline",
  waiting: "tag tag-outline",
};

/** How long the current node has been running, from its last node_started. */
function nodeRuntime(events: KraftEvent[], nodeId: string | null): string | null {
  if (!nodeId) return null;
  const start = [...events]
    .reverse()
    .find((e) => e.type === "node_started" && e.payload.node_id === nodeId);
  if (!start) return null;
  const ms = Date.now() - Date.parse(start.created_at);
  return Number.isNaN(ms) ? null : elapsed(ms);
}

/** The label. Read-only until asked from the ⋯ menu or the hover pencil. */
function Title({
  item,
  editing,
  onEdit,
  onDone,
}: {
  item: WorkItem;
  editing: boolean;
  onEdit: () => void;
  onDone: () => void;
}) {
  const hydrateItem = useStore((s) => s.hydrateItem);
  const [draft, setDraft] = useState(item.title);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (editing) setDraft(item.title);
  }, [editing, item.title]);

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.updateWorkItem(item.id, { title: draft });
      await hydrateItem(item.id);
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (editing) {
    return (
      <div className="field" data-testid="item-title">
        <label htmlFor="item-title-edit">Title</label>
        <input
          id="item-title-edit"
          className="input"
          aria-label="title"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <div className="gate-actions capped-actions">
          <button className="btn btn-primary" disabled={busy || !draft.trim()} onClick={save}>
            Save
          </button>
          <button className="btn" disabled={busy} onClick={onDone}>
            Cancel
          </button>
        </div>
        {err && <p className="form-error">{err}</p>}
      </div>
    );
  }

  return (
    // `.detail h2` is an exact-text match across most of the e2e suite (the
    // one element naming which work item is on screen) — keep it a plain
    // sibling of the pencil, not wrapped in a button.
    <div className="detail-title-row" data-testid="item-title">
      <h2 className="detail-title">{item.title}</h2>
      <button className="btn btn-quiet detail-title-pencil" aria-label="edit title" onClick={onEdit}>
        <PencilSimple size={13} />
      </button>
    </div>
  );
}

/** The brief. Read-only until asked, because editing it changes what every
 *  later node is told. */
function Description({
  item,
  editing,
  onDone,
}: {
  item: WorkItem;
  editing: boolean;
  onDone: () => void;
}) {
  const hydrateItem = useStore((s) => s.hydrateItem);
  const [draft, setDraft] = useState(item.description ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (editing) setDraft(item.description ?? "");
  }, [editing, item.description]);

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.updateWorkItem(item.id, { description: draft });
      await hydrateItem(item.id);
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (editing) {
    return (
      <div className="field">
        <label htmlFor="item-description-edit">Description</label>
        <textarea
          id="item-description-edit"
          className="input"
          aria-label="description"
          rows={4}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <div className="gate-actions capped-actions">
          <button className="btn btn-primary" disabled={busy} onClick={save}>
            Save
          </button>
          <button className="btn" disabled={busy} onClick={onDone}>
            Cancel
          </button>
        </div>
        {err && <p className="form-error">{err}</p>}
      </div>
    );
  }

  if (!item.description) return null;

  return (
    <p className="detail-description" data-testid="item-description">
      {item.description}
    </p>
  );
}

export function Header({
  item,
  events,
  collapsed = false,
  onWheel,
  onExpand,
}: {
  item: WorkItem;
  events: KraftEvent[];
  /** WI-3 · Kraft-yx8v: the title/description hide once the top block has
   *  been scrolled past. Owned by the page (`index.tsx`), not this
   *  component, since the collapsed flag is read by `.detail`'s
   *  `data-head` attribute one level up. */
  collapsed?: boolean;
  onWheel?: (e: WheelEvent<HTMLDivElement>) => void;
  onExpand?: () => void;
}) {
  const nodes = item.chain_definition.nodes;
  const at = nodes.findIndex((n) => n.id === item.current_node_id);
  const runtime = nodeRuntime(events, item.current_node_id);
  const mr = [...events].reverse().find((e) => e.type === "mr_opened");
  // Everything not the one frequent action lives in the ⋯ menu (spec §2);
  // editing the title/description moved here from an always-visible Edit
  // button so the header's flow is free for the two-column grid.
  const [editing, setEditing] = useState<"title" | "description" | null>(null);

  return (
    <div className="detail-head" onWheel={onWheel}>
      <div className="detail-meta">
        <span title={item.repo}>{repoName(item.repo)}</span>
        {!!item.repos?.length && (
          <span className="tag tag-neutral tag-tight">+{item.repos.length - 1} submodules</span>
        )}
        <span>{item.chain_template}</span>
        {mr && (
          <a className="mr-link" href={String(mr.payload.url)} target="_blank" rel="noreferrer">
            !{String(mr.payload.number)}
          </a>
        )}
        {item.bead_id && <code title={`work item ${item.id}`}>{item.bead_id}</code>}
        {item.attachments?.length ? (
          <span className="tag tag-outline tag-tight">
            from {item.attachments.map((a) => a.kind).join("+")}
          </span>
        ) : null}
        <span className={`${STATUS_TAG[item.status]} detail-status`}>
          {statusWord(item.status)}
        </span>
        {/* A wheel gesture collapses the block, but that's unreachable from
            the keyboard on its own -- this chevron is the way back in
            without one. */}
        {collapsed && (
          <button
            type="button"
            className="btn btn-quiet detail-head-expand"
            aria-label="expand title and description"
            onClick={onExpand}
          >
            <CaretDown size={13} />
          </button>
        )}
        <OverflowMenu
          items={[
            { label: "Edit title", onSelect: () => setEditing("title") },
            { label: "Edit description", onSelect: () => setEditing("description") },
          ]}
        />
      </div>
      <div className="detail-head-collapsible" data-collapsed={collapsed && !editing}>
        <Title
          item={item}
          editing={editing === "title"}
          onEdit={() => setEditing("title")}
          onDone={() => setEditing(null)}
        />
        <Description
          item={item}
          editing={editing === "description"}
          onDone={() => setEditing(null)}
        />
      </div>
      <div className="detail-hero">
        <span className="hero-node">{item.current_node_id ?? "—"}</span>
        {item.fixCycle != null && (
          <span className="tag tag-outline">fix · cycle {item.fixCycle}</span>
        )}
        {item.cappedOut && (
          <span className="tag tag-outline">
            <Prohibit size={11} />
            capped {item.cappedOut.cycles}/{item.cappedOut.attempts}
          </span>
        )}
        <span className="hero-sub">
          {at >= 0 && `node ${at + 1} of ${nodes.length}`}
          {/* "running 6s" on an item that stopped an hour ago is a lie the
              clock keeps telling. Only an active item is running. */}
          {runtime && (item.status === "active" ? ` · running ${runtime}` : ` · ${runtime}`)}
          {at < 0 && !item.current_node_id && " · not started"}
        </span>
        {item.progress && (
          <>
            <TaskLine progress={item.progress} />
            <TaskBar progress={item.progress} />
          </>
        )}
      </div>
    </div>
  );
}
