import { useEffect, useId, useState, type ReactNode, type WheelEvent } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CaretDown, PencilSimple, Prohibit, Robot } from "@phosphor-icons/react";
import * as api from "../../api";
import { deriveState, type ItemDisplayState } from "../../deriveState";
import { ago, elapsedBetween, nodeRunSpan, repoName, statusWord } from "../../format";
import { useStore } from "../../store";
import { OverflowMenu, TaskBar, TaskLine } from "../../components/ui";
import { ShortId } from "../../components/ShortId";
import type { KraftEvent, WorkerSession, WorkItem } from "../../types";

/**
 * The item page's header (W11 · A, design 1e): one meta line and, on the same
 * row, the status run -- state chip · node · position · time · cycle pill.
 * Under them the title, a "description" link that opens the brief, and the
 * task progress row (W0.1).
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
  extra,
}: {
  item: WorkItem;
  editing: boolean;
  onEdit: () => void;
  onDone: () => void;
  /** The "description" link, after the title. */
  extra?: ReactNode;
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
      {extra}
    </div>
  );
}

/** The brief's editor. Read-only until asked, because editing it changes what
 *  every later node is told. */
function DescriptionEditor({ item, onDone }: { item: WorkItem; onDone: () => void }) {
  const hydrateItem = useStore((s) => s.hydrateItem);
  const [draft, setDraft] = useState(item.description ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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

/** `node · 5/8 · 6m` (W11 rule 2). A finished item names the node it finished
 *  on -- never "—" or "not started" (W0.5) -- and a node's time is the shared
 *  run-time helper, frozen once its session exits (W0.4). */
function runLine(item: WorkItem, events: KraftEvent[], sessions: WorkerSession[], state: ItemDisplayState): string {
  const nodes = item.chain_definition.nodes;
  const last = (type: string) => [...events].reverse().find((e) => e.type === type);
  if (state === "not_started") {
    return [nodes[0] && `starts at ${nodes[0].id}`, `filed ${ago(item.created_at)}`.trim()].filter(Boolean).join(" · ");
  }
  const node =
    item.current_node_id ?? (last("node_completed")?.payload.node_id as string | undefined) ?? nodes.at(-1)?.id ?? "—";
  const at = nodes.findIndex((n) => n.id === node);
  let time: string | null = null;
  if (state === "archived") time = "archived";
  else if (state === "done") time = `completed ${ago(last("work_item_completed")?.created_at ?? item.updated_at)}`;
  else if (state === "abandoned") time = `abandoned ${ago(last("work_item_abandoned")?.created_at ?? item.updated_at)}`;
  else {
    const span = nodeRunSpan(item.current_node_id, events, sessions);
    if (span) time = elapsedBetween(span.from, span.to);
  }
  return [node, at >= 0 && `${at + 1}/${nodes.length}`, time?.trim()].filter(Boolean).join(" · ");
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
  /** The "+N submodules" link opens Config → Repos (W0.7). */
  onShowRepos?: () => void;
}) {
  const { state } = deriveState(item, sessions, events);
  const submodules = (item.repos?.length ?? 0) - 1;
  const from = item.attachments?.length ? `from ${item.attachments.map((a) => a.kind).join("+")}` : null;
  // Everything not the one frequent action lives in the ⋯ menu (spec §2).
  const [editing, setEditing] = useState<"title" | "description" | null>(null);
  // The brief is closed by default; the link after the title opens it (rule 3).
  const [descOpen, setDescOpen] = useState(false);
  const descId = useId();

  // One line, ellipsized (rule 1). Each part carries its own separator, so a
  // part the phone hides (W3.2: bead, submodules, provenance) takes its `·`.
  // A deliberate cut: the part's whole text is its title (sweep/README.md's
  // data-allow-ellipsis allowlist, W11).
  const part = (key: string, node: ReactNode, full: string, desktopOnly = false) => (
    <span
      key={key}
      className={desktopOnly ? "detail-meta-part desktop-only" : "detail-meta-part"}
      title={full}
      data-allow-ellipsis
    >
      {node}
    </span>
  );
  const metaTitle = [
    repoName(item.repo),
    item.chain_template,
    item.bead_id,
    item.id,
    submodules > 0 && `+${submodules} submodule${submodules === 1 ? "" : "s"}`,
    from,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="detail-head" onWheel={onWheel}>
      <div className="detail-meta" title={metaTitle}>
        {part("repo", repoName(item.repo), item.repo)}
        {part("template", item.chain_template, item.chain_template)}
        {item.bead_id && part("bead", <code>{item.bead_id}</code>, item.bead_id, true)}
        {part("id", <ShortId id={item.id} />, item.id)}
        {submodules > 0 &&
          part(
            "submodules",
            <button type="button" className="detail-submodules" onClick={onShowRepos}>
              +{submodules} submodule{submodules === 1 ? "" : "s"}
            </button>,
            `+${submodules} submodule${submodules === 1 ? "" : "s"}`,
            true,
          )}
        {from && part("from", from, from, true)}
      </div>
      <div className="detail-run">
        {/* A never-started item is `paused` in the database, but "paused"
            beside "not started" reads as two different things (21). */}
        {state === "escalating" ? (
          // An agent is on it, not waiting on you (W11 · J.5).
          <span className="tag tag-escalating detail-status">
            <Robot size={11} aria-hidden />
            escalating
          </span>
        ) : (
          <span className={`${STATUS_TAG[item.status]} detail-status`}>
            {state === "not_started" ? "waiting to start" : statusWord(item.status)}
          </span>
        )}
        <span className="detail-run-node">{runLine(item, events, sessions, state)}</span>
        {item.fixCycle != null && <span className="tag tag-outline">fix · cycle {item.fixCycle}</span>}
        {item.cappedOut && (
          <span className="tag tag-outline">
            <Prohibit size={11} />
            capped {item.cappedOut.cycles}/{item.cappedOut.attempts}
          </span>
        )}
        {/* A wheel gesture collapses the block, but that's unreachable from
            the keyboard on its own -- this chevron is the way back in. */}
        {collapsed && (
          <button type="button" className="btn btn-quiet detail-head-expand" aria-label="expand title and description" onClick={onExpand}>
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
          extra={
            item.description && editing !== "description" ? (
              <button
                type="button"
                className="detail-description-link"
                aria-expanded={descOpen}
                aria-controls={descOpen ? descId : undefined}
                onClick={() => setDescOpen((v) => !v)}
              >
                description
              </button>
            ) : null
          }
        />
        {editing === "description" ? (
          <DescriptionEditor item={item} onDone={() => setEditing(null)} />
        ) : (
          descOpen &&
          item.description && (
            // Rendered markdown, never raw `##`/`**` (W0.1).
            <div id={descId} className="detail-description" data-testid="item-description">
              <Markdown remarkPlugins={[remarkGfm]}>{item.description}</Markdown>
            </div>
          )
        )}
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
