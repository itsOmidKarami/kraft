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
  Coins,
  CircleNotch,
  DotsThree,
  Flag,
  Pause,
  Prohibit,
  Question,
  WarningCircle,
  X,
  XCircle,
} from "@phosphor-icons/react";
import { statusWord } from "../format";
import type { ItemDisplayState } from "../deriveState";
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

const GLYPHS: Record<SessionStatus | ItemDisplayState, typeof Check> = {
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
  // A task that never launched -- no agent can fix a missing binary or cwd by
  // editing source, so it reads the same as capped_out: stopped, not failed.
  config_error: Prohibit,
  // Parked on a pipeline, woken by a poller -- the same shape as rate_limited,
  // so the same glyph: not running, not broken (Kraft-ru98).
  waiting: Clock,
  // Item-level design states (deriveState, UI v2 · 01). `capped`/`question`
  // are session-level `capped_out`/`needs_context` under a different name at
  // the work-item level -- distinct keys, same family of meaning.
  gate: Flag,
  capped: Prohibit,
  question: ChatText,
  budget: Coins,
  not_started: Circle,
  abandoned: X,
};

/** 20px ring, 1px border, Phosphor glyph. Only `running` animates. */
export function StatusGlyph({
  status,
  size = 13,
}: {
  status: SessionStatus | ItemDisplayState;
  size?: number;
}) {
  const Icon = GLYPHS[status] ?? Question;
  return (
    <span className="glyph" data-status={status} aria-label={status} role="img">
      <Icon size={size} />
    </span>
  );
}

/* — mini chain (spec §4) ————————————————————————————————————————————— */

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
 * editor nothing is current, so `invalid` is what shows. `paused` (set by the
 * caller from `deriveState(item).state` being one of paused/capped/abandoned/
 * rate_limited/waiting — the Prototype's `mini()` "off" set) drops the current
 * segment to the same dimmed rendering.
 */
export function MiniChain({
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
              {n.gate_after && (
                <span className="chain-gate" title={n.gate_after}>
                  <Flag weight="fill" size={9} />
                </span>
              )}
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

/* — switch (spec §4) —————————————————————————————————————————————————— */

/** 34×20 toggle, keyboard-operable via native `<button>`. Replaces the four
 *  hand-rolled `.switch` buttons `views/Settings.tsx` had (repos/plugins/
 *  intake/notify pages) — same markup, one definition. */
export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className="switch"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span className="switch-knob" />
    </button>
  );
}

/* — chip (spec §4, board filter row) ————————————————————————————————— */

/** Radius-999 pill with an optional trailing count and a selected state —
 *  the board's status/repo/template filter row (design 04). Colors are the
 *  Prototype's own `pill()` method: selected border accent-700/bg
 *  accent-900/text accent-300, unselected border neutral-800/text
 *  neutral-400. Not consumed inside UI v2 · 01 — the board filter row that
 *  uses it is UI v2 · 02. */
export function Chip({
  label,
  count,
  selected = false,
  onClick,
}: {
  label: ReactNode;
  count?: number;
  selected?: boolean;
  onClick?: () => void;
}) {
  return (
    <button type="button" className="chip" aria-pressed={selected} onClick={onClick}>
      {label}
      {count != null && <span className="chip-count">{count}</span>}
    </button>
  );
}
