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
 * Inspector · Timeline (UI v2 · 05, 15; W11 · F; W13 · C). Under "this node"
 * the node's rounds, newest first -- a round header folds its sessions and
 * findings -- with escalation turns and node-level events (gates, lifecycle)
 * at their time between them. Under "all", one folded row per node with its
 * rounds inside. `RightPane/Events.tsx` streams whatever is selected: a
 * session, a round, one event, or (nothing picked) the node.
 */

const hm = (iso: string | null | undefined) => (iso ? clock(iso).slice(0, 5) : "");
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function span(from: string | null, to: string | null): string {
  if (!from) return "";
  return `${hm(from)} → ${to ? hm(to) : "now"} · ${elapsedBetween(from, to)}`;
}

/** `judge: stop`, `judge: continue · 5 findings`, `running`. */
function outcome(r: Round, current: boolean): string {
  const found = r.findings.length ? plural(r.findings.length, "finding") : "";
  if (r.verdict === "continue") return ["judge: continue", found].filter(Boolean).join(" · ");
  if (r.verdict) return `judge: ${verdictWord(r.verdict)}`;
  if (current && r.sessions.some((s) => !s.exited_at)) return "running";
  return found;
}

function findingsLine(r: Round): string | null {
  if (!r.findings.length) return null;
  const critical = r.findings.filter((f) => f.severity === "critical").length;
  return [plural(r.findings.length, "finding"), critical ? `${critical} critical` : ""].filter(Boolean).join(" · ");
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

  // C.5: rows are buttons; Up/Down move between them, Left/Right fold and unfold.
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

  const roundBody = (r: Round) => {
    const line = findingsLine(r);
    return (
      <>
        {[...r.sessions].reverse().map((s) => sessionRow(s, s.hook_point))}
        {line && (
          <p className="timeline-findings" data-testid={`timeline-findings-${r.node}-${r.n}`}>
            {line}
          </p>
        )}
      </>
    );
  };

  const entryRow = (nt: NodeRounds, en: TimelineEntry) => {
    if (en.kind === "round") {
      const r = en.round;
      // One round: no header at all, its sessions stand in the list (C.1).
      if (nt.rounds.length === 1) return <Fragment key={`r${r.n}`}>{roundBody(r)}</Fragment>;
      const key = `round:${r.node}:${r.n}`;
      const current = r === nt.rounds[nt.rounds.length - 1];
      const open = isOpen(key, current);
      return (
        <Fragment key={key}>
          <button
            type="button"
            data-trow
            data-fold={key}
            aria-expanded={open}
            className="row timeline-row timeline-round"
            data-testid={`timeline-round-${r.node}-${r.n}`}
            data-selected={sel?.kind === "round" && sel.node === r.node && sel.n === r.n}
            onClick={() => {
              onSelect(key);
              setOpen(key, !open);
            }}
          >
            <span className="timeline-caret" aria-hidden>
              {open ? "▾" : "▸"}
            </span>
            <RowText title={`round ${r.n + 1}`} sub={span(r.startedAt, r.endedAt)} />
            <span className="row-sub">{outcome(r, current)}</span>
          </button>
          {open && roundBody(r)}
        </Fragment>
      );
    }
    if (en.kind === "escalation") return sessionRow(en.session, `escalation · turn ${en.turn}`);
    const e = en.event;
    const label = e.type === "task_progress" ? taskRunLabel(e, en.last) : en.label;
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
