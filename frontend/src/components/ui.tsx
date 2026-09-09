/**
 * The shared primitives every Kraft screen is built from (handoff spec §2–§4).
 *
 * There is one list row in this interface. Board rows, tasks, timeline events,
 * documents, repos, plugin bindings, policy caps and sessions are all `Row`
 * with different children — open rows on a fading hairline, never a bordered
 * box. Colour, size and radius come from Nocturne tokens in `styles.css`; this
 * file carries no literal values.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Check,
  ChatText,
  Circle,
  Clock,
  CircleNotch,
  DotsThree,
  Pause,
  Prohibit,
  Question,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react";
import { statusWord } from "../format";
import type { ChainNode, SessionStatus } from "../types";

/* — rows ————————————————————————————————————————————————————————————— */

export function Row({
  columns,
  className,
  children,
  ...rest
}: { columns?: string } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={["row", className].filter(Boolean).join(" ")}
      style={columns ? { gridTemplateColumns: columns } : undefined}
      {...rest}
    >
      {children}
    </div>
  );
}

/** 14px title over a 12px sub-line. The sub-line is where detail goes. */
export function RowText({ title, sub }: { title: ReactNode; sub?: ReactNode }) {
  return (
    <div className="row-text">
      <span className="row-title">{title}</span>
      {sub != null && <span className="row-sub">{sub}</span>}
    </div>
  );
}

/** Plain text in the state colour — never a tag. Tags are reserved (spec §2). */
export function RowState({ status, children }: { status: SessionStatus; children?: ReactNode }) {
  return (
    <span className="row-state" data-status={status}>
      {children ?? statusWord(status)}
    </span>
  );
}

/* — status ————————————————————————————————————————————————————————————— */

const GLYPHS: Record<SessionStatus, typeof Check> = {
  running: CircleNotch,
  done: Check,
  done_with_concerns: WarningCircle,
  needs_context: ChatText,
  failed: XCircle,
  capped_out: Prohibit,
  paused: Pause,
  unknown: Question,
  // Design gap: `pending` is not in the spec's status table. It takes the
  // quietest ring in the vocabulary rather than inventing a glyph.
  pending: Circle,
  rate_limited: Clock,
};

/** 20px ring, 1px border, Phosphor glyph. Only `running` animates. */
export function StatusGlyph({ status, size = 13 }: { status: SessionStatus; size?: number }) {
  const Icon = GLYPHS[status] ?? Question;
  return (
    <span className="glyph" data-status={status} aria-label={status} role="img">
      <Icon size={size} />
    </span>
  );
}

/* — chain bar (spec §4) ——————————————————————————————————————————————— */

/**
 * One segment per node, equal width. Fix cycles and concurrent tasks are
 * deliberately not drawn here — they appear beside the current-node name.
 *
 * Takes the node list rather than a `WorkItem`: the chain-template editor draws
 * a draft that has no work item behind it (Kraft-3e6e). Explicit props rather
 * than an optional `nodes` override beside `item`, so there is one way to call
 * this.
 *
 * Segment state precedence: invalid, then current, then done, then todo. In the
 * editor nothing is current, so `invalid` is what shows.
 */
export function ChainBar({
  nodes,
  currentNodeId = null,
  done = [],
  invalid = [],
  size,
  paused = false,
}: {
  nodes: ChainNode[];
  currentNodeId?: string | null;
  done?: string[];
  invalid?: string[];
  size: "sm" | "lg";
  paused?: boolean;
}) {
  const doneIds = new Set(done);
  const invalidIds = new Set(invalid);
  return (
    <div className={`chain-bar ${size}`} data-testid="chain-bar">
      {nodes.map((n) => {
        const state = invalidIds.has(n.id)
          ? "invalid"
          : n.id === currentNodeId
            ? (paused ? "paused" : "current")
            : doneIds.has(n.id)
              ? "done"
              : "todo";
        return (
          <span
            key={n.id}
            className="chain-seg"
            data-node={n.id}
            data-testid={`node-${n.id}`}
            data-state={state}
          >
            <span className="chain-fill" title={size === "sm" ? n.id : undefined}>
              {n.gate_after && <span className="chain-gate" title={n.gate_after} />}
            </span>
            {size === "lg" && <span className="chain-label">{n.id}</span>}
          </span>
        );
      })}
    </div>
  );
}

/* — chrome ————————————————————————————————————————————————————————————— */

export function SectionLabel({
  children,
  tone,
}: {
  children: ReactNode;
  tone?: "accent";
}) {
  return (
    <div className="section-label" data-tone={tone}>
      {children}
    </div>
  );
}

export interface OverflowItem {
  label: string;
  onSelect: () => void;
  danger?: boolean;
  /** Ask first. Set this on anything with no undo behind it — the menu swaps to
   *  a confirm row naming what is about to happen, the same two-step the gate's
   *  reject uses, rather than firing on the first click. */
  confirm?: string;
}

/**
 * Everything that is not the one frequent action lives here. Destructive
 * actions are never permanently visible (spec §2).
 */
export function OverflowMenu({ items, label = "More" }: { items: OverflowItem[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState<OverflowItem | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) {
        setOpen(false);
        setPending(null);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
        setPending(null);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="overflow" ref={ref}>
      <button
        className="btn btn-ghost overflow-btn"
        aria-label={label}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((v) => !v)}
      >
        <DotsThree size={16} />
      </button>
      {open && (
        <div className="overflow-menu" role="menu">
          {pending ? (
            <>
              <p className="overflow-confirm">{pending.confirm}</p>
              <button
                role="menuitem"
                data-danger={pending.danger || undefined}
                onClick={() => {
                  const it = pending;
                  setPending(null);
                  setOpen(false);
                  it.onSelect();
                }}
              >
                {pending.label}
              </button>
              <button role="menuitem" onClick={() => setPending(null)}>
                Cancel
              </button>
            </>
          ) : (
            items.map((it) => (
              <button
                key={it.label}
                role="menuitem"
                data-danger={it.danger || undefined}
                onClick={() => {
                  if (it.confirm) {
                    setPending(it);
                    return;
                  }
                  setOpen(false);
                  it.onSelect();
                }}
              >
                {it.label}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

export interface Tab {
  id: string;
  label: string;
  count?: number;
}

export function Tabs({
  tabs,
  value,
  onChange,
}: {
  tabs: Tab[];
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={t.id === value}
          className="tab"
          onClick={() => onChange(t.id)}
        >
          {t.label}
          {t.count != null && <span className="tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}
