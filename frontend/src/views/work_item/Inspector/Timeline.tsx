import { Fragment, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { RowText } from "../../../components/ui";
import { clock, elapsedBetween, statusWord } from "../../../format";
import type { KraftEvent, WorkerSession } from "../../../types";
import {
  groupByNode,
  nodeRounds,
  parseTimelineSelection,
  taskRunLabel,
  verdictWord,
  type NodeRounds,
  type Round,
  type TimelineEntry,
} from "../timelineHelpers";
import { ScopeChips, type Scope } from "./Tasks";

/**
 * Inspector · Timeline (UI v2 · 05, 15; W11 · F; W13 · C; W14 · A). Under
 * "this node" one row per round, newest first -- `round 2 · 16:02 → 16:09 · 7m`
 * with its sessions, findings and verdict on the right -- and escalation turns
 * and node-level events outside every round at their time between them. The
 * sessions themselves are rows in the right pane (`RightPane/Events.tsx`),
 * which streams whatever is selected. Under "all", one folded row per node.
 */

const hm = (iso: string | null | undefined) => (iso ? clock(iso).slice(0, 5) : "");
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function span(from: string | null, to: string | null): string {
  if (!from) return "";
  return `${hm(from)} → ${to ? hm(to) : "now"} · ${elapsedBetween(from, to)}`;
}

/** `2 sessions · 5 findings · judge: continue`, `1 session · 0 findings · running`. */
function outcome(r: Round, current: boolean): string {
  const judge = r.verdict
    ? `judge: ${verdictWord(r.verdict)}`
    : current && r.sessions.some((s) => !s.exited_at)
      ? "running"
      : null;
  return [plural(r.sessions.length, "session"), plural(r.findings.length, "finding"), judge].filter(Boolean).join(" · ");
}

export function Timeline({
  events,
  sessions = [],
  nodeId,
  scope,
  onScope,
  selected,
  onSelect,
}: {
  events: KraftEvent[];
  sessions?: WorkerSession[];
  nodeId: string | null;
  scope: Scope;
  onScope: (s: Scope) => void;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const nodes = groupByNode(events).map((g) => nodeRounds(g.node, events, sessions));
  const own = nodes.find((n) => n.node === nodeId) ?? null;
  const sel = parseTimelineSelection(selected);
  const [folds, setFolds] = useState<Record<string, boolean>>({});
  const listRef = useRef<HTMLDivElement>(null);

  // A card's "see Timeline" link to another node's stream (a bare node) shows
  // it under "all" -- unless the person just chose "this node" themselves.
  const lastScope = useRef(scope);
  useEffect(() => {
    const scopeChanged = lastScope.current !== scope;
    lastScope.current = scope;
    if (scope === "node" && !scopeChanged && sel?.kind === "node" && sel.node !== nodeId) onScope("all");
  });

  const isOpen = (key: string, byDefault: boolean) => folds[key] ?? byDefault;
  const setOpen = (key: string, open: boolean) => setFolds((f) => ({ ...f, [key]: open }));

  // C.5: rows are buttons; Up/Down move between them, Left/Right fold a node under "all".
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const rows = [...(listRef.current?.querySelectorAll<HTMLButtonElement>("button[data-trow]") ?? [])];
    const at = rows.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = at < 0 ? 0 : Math.min(rows.length - 1, Math.max(0, at + (e.key === "ArrowDown" ? 1 : -1)));
      rows[next]?.focus();
    } else if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && at >= 0 && rows[at].dataset.fold) {
      e.preventDefault();
      setOpen(rows[at].dataset.fold!, e.key === "ArrowRight");
    }
  };

  const sessionRow = (s: WorkerSession, title: string) => (
    <button
      key={s.id}
      type="button"
      data-trow
      className="row timeline-row timeline-session"
      data-testid={`timeline-session-${s.id}`}
      data-selected={sel?.kind === "session" && sel.id === s.id}
      onClick={() => onSelect(`session:${s.id}`)}
    >
      <RowText
        title={title}
        sub={`${statusWord(s.status)} · ${elapsedBetween(s.started_at ?? s.created_at, s.exited_at)}`}
      />
      <span className="row-sub">{hm(s.exited_at ?? s.created_at)}</span>
    </button>
  );

  const threadBody = (sessions: WorkerSession[]) => (
    <>
      {[...sessions]
        .map((s, i) => sessionRow(s, `escalation · turn ${i + 1}`))
        .reverse()}
    </>
  );

  const entryRow = (nt: NodeRounds, en: TimelineEntry) => {
    if (en.kind === "round") {
      const r = en.round;
      const key = `round:${r.node}:${r.n}`;
      const current = r === nt.rounds[nt.rounds.length - 1];
      // A session picked in the right pane keeps its round lit here.
      const picked =
        (sel?.kind === "round" && sel.node === r.node && sel.n === r.n) ||
        (sel?.kind === "session" && r.sessions.some((s) => s.id === sel.id));
      return (
        <button
          key={key}
          type="button"
          data-trow
          className="row timeline-row timeline-round"
          data-testid={`timeline-round-${r.node}-${r.n}`}
          data-selected={picked}
          onClick={() => onSelect(key)}
        >
          <RowText title={[`round ${r.n + 1}`, span(r.startedAt, r.endedAt)].filter(Boolean).join(" · ")} />
          <span className="row-sub">{outcome(r, current)}</span>
        </button>
      );
    }
    if (en.kind === "escalationThread") {
      const last = en.sessions.at(-1)!;
      const running = en.sessions.some((s) => !s.exited_at);
      const key = `escalation-thread:${last.node_id}:${en.thread}`;
      const threadEntries = nt.entries.filter((e) => e.kind === "escalationThread");
      const current = en === threadEntries.at(-1);
      const open = isOpen(key, current);
      // A single escalation thread on this node: no header, its turns stand
      // in the list directly.
      if (threadEntries.length === 1) return <Fragment key={key}>{threadBody(en.sessions)}</Fragment>;
      return (
        <Fragment key={key}>
          <button
            type="button"
            data-trow
            data-fold={key}
            aria-expanded={open}
            className="row timeline-row timeline-round"
            data-testid={`timeline-escalation-thread-${en.thread}`}
            data-selected={sel?.kind === "session" && en.sessions.some((s) => s.id === sel.id)}
            onClick={() => {
              onSelect(`session:${last.id}`);
              setOpen(key, !open);
            }}
          >
            <span className="timeline-caret" aria-hidden>
              {open ? "▾" : "▸"}
            </span>
            <RowText title={`escalation · thread ${en.thread}`} sub={`${en.sessions.length} turn${en.sessions.length === 1 ? "" : "s"}`} />
            <span className="row-sub">{running ? "running" : "done"}</span>
          </button>
          {open && threadBody(en.sessions)}
        </Fragment>
      );
    }
    const e = en.event;
    const label = e.type === "plan_progress" ? taskRunLabel(e, en.last) : en.label;
    return (
      <button
        key={`e${e.seq}`}
        type="button"
        data-trow
        className="row timeline-row timeline-event"
        data-testid={`timeline-event-${e.seq}`}
        data-selected={sel?.kind === "event" && sel.seq === e.seq}
        onClick={() => onSelect(`event:${e.seq}`)}
      >
        <RowText title={label} />
        <span className="row-sub">{hm(e.created_at)}</span>
      </button>
    );
  };

  const nodeList = (nt: NodeRounds) => [...nt.entries].reverse().map((en) => entryRow(nt, en));

  const count = scope === "node" ? (own?.rounds.length ?? 0) : nodes.reduce((n, nt) => n + nt.rounds.length, 0);

  return (
    <div className="inspector-list" data-testid="inspector-timeline" ref={listRef} onKeyDown={onKeyDown}>
      <p className="section-label">
        ROUNDS · {count}
        <ScopeChips scope={scope} onScope={onScope} nodeId={nodeId} />
      </p>
      {scope === "node" ? (
        own ? nodeList(own) : <p className="empty">no events on this node yet</p>
      ) : nodes.length === 0 ? (
        <p className="empty">no events yet</p>
      ) : (
        <>
          {nodes.map((nt) => {
            const key = `node:${nt.node}`;
            const open = isOpen(key, nt.node === nodeId);
            const what = nt.rounds.length ? plural(nt.rounds.length, "round") : plural(nt.events.length, "event");
            return (
              <Fragment key={nt.node}>
                <button
                  type="button"
                  data-trow
                  data-fold={key}
                  aria-expanded={open}
                  className="row timeline-row timeline-node-row"
                  data-testid={`timeline-node-${nt.node}`}
                  data-selected={sel?.kind === "node" && sel.node === nt.node}
                  onClick={() => {
                    onSelect(nt.node);
                    setOpen(key, !open);
                  }}
                >
                  <span className="timeline-caret" aria-hidden>
                    {open ? "▾" : "▸"}
                  </span>
                  <RowText title={`${nt.node} · ${what}${nt.startedAt ? ` · ${elapsedBetween(nt.startedAt, nt.endedAt)}` : ""}`} />
                </button>
                {open && <div className="timeline-nested">{nodeList(nt)}</div>}
              </Fragment>
            );
          })}
          <p className="inspector-foot">{events.length} events total</p>
        </>
      )}
    </div>
  );
}
