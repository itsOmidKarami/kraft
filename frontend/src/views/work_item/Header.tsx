import { useEffect, useLayoutEffect, useRef, useState, type WheelEvent } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CaretDown, PencilSimple, Prohibit } from "@phosphor-icons/react";
import * as api from "../../api";
import { deriveState } from "../../deriveState";
import { ago, elapsedBetween, nodeRunSpan, repoName, statusWord } from "../../format";
import { useStore } from "../../store";
import { OverflowMenu, TaskBar, TaskLine } from "../../components/ui";
import { ShortId } from "../../components/ShortId";
import { plainMarkdown } from "../../components/Snippet";
import type { KraftEvent, WorkerSession, WorkItem } from "../../types";

/**
 * Desktop 11's header (UI v2 · 05): the meta line, the title/description
 * editors carried over from the old `WorkItemDetail.tsx`, the hero node block
 * on the right, and the task progress row under the title (W0.1).
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
        {/* A textarea, not an input: a 140-character title was cut off in a
            one-line box (W0.11). Grows to three lines; Enter saves,
            Shift+Enter is a newline, Escape cancels. */}
        <textarea
          id="item-title-edit"
          className="input detail-title-input"
          aria-label="title"
          rows={1}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (!busy && draft.trim()) void save();
            } else if (e.key === "Escape") {
              e.preventDefault();
              onDone();
            }
          }}
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
      <h2 className="detail-title" title={item.title}>
        {item.title}
      </h2>
      <button className="btn btn-quiet detail-title-pencil" aria-label="edit title" onClick={onEdit}>
        <PencilSimple size={13} />
      </button>
    </div>
  );
}

/** The brief as rendered markdown, clamped to two lines with "more" to expand
 *  it in place (W0.1) — never raw `##`/`**`. Clamped, its blocks flow inline
 *  (work_item.css) so the two lines are prose, not a heading and a blank. */
function DescriptionText({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [clamped, setClamped] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setClamped(el.scrollHeight > el.clientHeight + 1);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [text]);

  return (
    <div className="detail-description-wrap">
      <div ref={ref} className="detail-description" data-expanded={expanded} data-testid="item-description">
        {/* One block child: a -webkit-box blockifies its direct children,
            so the markdown's own blocks sit one level down, where the
            clamped rules can flow them inline. */}
        <div className="detail-description-md">
          {/* Clamped, it is a two-line preview: prose only, headings and list
              markers stripped ("Context The verify node…" read as one run-on
              sentence). Expanded keeps the full markdown. */}
          {expanded ? (
            <Markdown remarkPlugins={[remarkGfm]}>{text}</Markdown>
          ) : (
            <p>{plainMarkdown(text.replace(/^\s{0,3}#{1,6}\s.*$/gm, "")).trim()}</p>
          )}
        </div>
      </div>
      {(clamped || expanded) && (
        <button
          type="button"
          className="btn btn-quiet detail-description-more"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "less" : "more"}
        </button>
      )}
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
  return <DescriptionText text={item.description} />;
}

/** The hero's node and the line under it (W0.4, W0.5). A finished item names
 *  the node it finished on — never "—" or "not started" — and a running
 *  node's duration is the shared run-time helper, frozen once its session
 *  exits. */
function hero(item: WorkItem, events: KraftEvent[], sessions: WorkerSession[]): { node: string; sub: string } {
  const { state } = deriveState(item, sessions, events);
  const nodes = item.chain_definition.nodes;
  const last = (type: string) => [...events].reverse().find((e) => e.type === type);
  const lastNode =
    item.current_node_id ?? (last("node_completed")?.payload.node_id as string | undefined) ?? nodes.at(-1)?.id ?? "—";

  if (state === "archived") return { node: lastNode, sub: "archived · read-only" };
  if (state === "done") {
    return { node: lastNode, sub: `completed ${ago(last("work_item_completed")?.created_at ?? item.updated_at)}` };
  }
  if (state === "not_started") {
    return { node: "not started", sub: `filed ${ago(item.created_at)}${nodes[0] ? ` · starts at ${nodes[0].id}` : ""}` };
  }

  const at = nodes.findIndex((n) => n.id === item.current_node_id);
  const parts = at >= 0 ? [`node ${at + 1} of ${nodes.length}`] : [];
  if (state === "abandoned") {
    parts.push(`abandoned ${ago(last("work_item_abandoned")?.created_at ?? item.updated_at)}`);
  } else {
    const span = nodeRunSpan(item.current_node_id, events, sessions);
    if (span) {
      const d = elapsedBetween(span.from, span.to);
      // "running 6s" on an item that stopped an hour ago is a lie the clock
      // keeps telling. Only a node whose session still runs is running.
      parts.push(span.to === null && item.status === "active" ? `running ${d}` : d);
    }
  }
  return { node: lastNode, sub: parts.join(" · ") };
}

export function Header({
  item,
  events,
  sessions = [],
  collapsed = false,
  onWheel,
  onExpand,
  onShowRepos,
}: {
  item: WorkItem;
  events: KraftEvent[];
  sessions?: WorkerSession[];
  /** WI-3 · Kraft-yx8v: the title/description hide once the top block has
   *  been scrolled past. Owned by the page (`index.tsx`), not this
   *  component, since the collapsed flag is read by `.detail`'s
   *  `data-head` attribute one level up. */
  collapsed?: boolean;
  onWheel?: (e: WheelEvent<HTMLDivElement>) => void;
  onExpand?: () => void;
  /** The "+N submodules" chip opens Config → Repos (W0.1, W0.7). */
  onShowRepos?: () => void;
}) {
  const mr = [...events].reverse().find((e) => e.type === "mr_opened");
  const { state } = deriveState(item, sessions, events);
  const { node, sub } = hero(item, events, sessions);
  const submodules = (item.repos?.length ?? 0) - 1;
  // Everything not the one frequent action lives in the ⋯ menu (spec §2);
  // editing the title/description moved here from an always-visible Edit
  // button so the header's flow is free for the two-column grid.
  const [editing, setEditing] = useState<"title" | "description" | null>(null);

  return (
    <div className="detail-head" onWheel={onWheel}>
      <div className="detail-meta">
        <span title={item.repo}>{repoName(item.repo)}</span>
        {submodules > 0 && (
          <button type="button" className="tag tag-neutral tag-tight detail-submodules desktop-only" onClick={onShowRepos}>
            +{submodules} submodule{submodules === 1 ? "" : "s"}
          </button>
        )}
        <span>{item.chain_template}</span>
        {mr && (
          <a className="mr-link" href={String(mr.payload.url)} target="_blank" rel="noreferrer">
            !{String(mr.payload.number)}
          </a>
        )}
        {/* Phone meta (W3.2): repo · template · id · status only. */}
        {item.bead_id && <code className="desktop-only">{item.bead_id}</code>}
        <ShortId id={item.id} />
        {item.attachments?.length ? (
          <span className="tag tag-outline tag-tight desktop-only">
            from {item.attachments.map((a) => a.kind).join("+")}
          </span>
        ) : null}
        {/* A never-started item is `paused` in the database, but "paused"
            beside "not started" reads as two different things (21). */}
        <span className={`${STATUS_TAG[item.status]} detail-status`}>
          {state === "not_started" ? "waiting to start" : statusWord(item.status)}
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
        <span className="hero-node">{node}</span>
        {item.fixCycle != null && (
          <span className="tag tag-outline">fix · cycle {item.fixCycle}</span>
        )}
        {item.cappedOut && (
          <span className="tag tag-outline">
            <Prohibit size={11} />
            capped {item.cappedOut.cycles}/{item.cappedOut.attempts}
          </span>
        )}
        <span className="hero-sub">{sub}</span>
      </div>
      {/* Full width under the title, never a column beside it: a long task
          title squeezed the item title into 280px (W0.1). */}
      {item.progress && (
        <div className="detail-progress">
          <TaskLine progress={item.progress} />
          <TaskBar progress={item.progress} />
        </div>
      )}
    </div>
  );
}
