/**
 * The shared primitives every Kraft screen is built from (handoff spec §2–§4).
 *
 * There is one list row in this interface. Board rows, tasks, timeline events,
 * documents, repos, plugin bindings, policy caps and sessions are all `Row`
 * with different children — open rows on a fading hairline, never a bordered
 * box. Colour, size and radius come from Nocturne tokens in `styles.css`; this
 * file carries no literal values.
 */
import { Fragment, useEffect, useId, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";
import {
  Archive,
  CaretDown,
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
  Robot,
  WarningCircle,
  X,
  XCircle,
} from "@phosphor-icons/react";
import { statusWord } from "../format";
import type { ItemDisplayState } from "../deriveState";
import type { ChainNode, SessionStatus, TaskProgress } from "../types";

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
  // A running/finished escalation turn -- same glyph as the escalate
  // controls it corresponds to (Escalate.tsx).
  escalating: Robot,
  escalated: Robot,
  archived: Archive,
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
      {nodes.map((n, i) => {
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
              {(n.gate_after || n.kind === "gate") && (
                <span className="chain-gate" title={n.gate_after ?? n.id}>
                  <Flag weight="fill" size={9} />
                </span>
              )}
            </span>
            {size === "lg" && (i === 0 || i === nodes.length - 1 || n.id === currentNodeId) && (
              <span className="chain-label">{n.id}</span>
            )}
          </span>
        );
      })}
    </div>
  );
}

/* — task progress (spec §7) —————————————————————————————————————————— */

/** One vocabulary for "where the implementer is in its plan" (UI v3 · §2):
 *  the count in accent, the title in neutral and ellipsized. `short` is the
 *  board row's meta line, where "Task 3/6" is all that fits. */
export function TaskLine({
  progress,
  form = "long",
}: {
  progress: TaskProgress;
  form?: "long" | "short";
}) {
  return (
    <span className="task-line">
      <span className="task-count">
        {form === "short"
          ? `Task ${progress.current}/${progress.total}`
          : `Task ${progress.current} of ${progress.total}`}
      </span>
      <span className="task-title">{progress.title}</span>
    </span>
  );
}

/** One 3px segment per task, gap 3 (UI v3 · §2). The current segment glows
 *  and pulses; everything after it is neutral-800. */
export function TaskBar({ progress }: { progress: TaskProgress }) {
  return (
    <span className="task-bar" data-testid="task-bar">
      {Array.from({ length: progress.total }, (_, i) => (
        <span
          key={i}
          className="task-seg"
          data-state={
            i + 1 < progress.current ? "done" : i + 1 === progress.current ? "current" : "pending"
          }
        />
      ))}
    </span>
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
  icon?: ReactNode;
  /** Shown, focusable and dimmed, but inert (Open MR before an MR exists). */
  disabled?: boolean;
  /** The row's tooltip, also its accessible description (W11 rule 7). */
  hint?: string;
  /** A hairline above this row, starting a new group. */
  divider?: boolean;
}

/**
 * Everything that is not the one frequent action lives here. Destructive
 * actions are never permanently visible (spec §2). A menu button (W11 rule 11):
 * opening focuses the first row, arrows move between rows, and Escape closes
 * it and puts focus back on the button.
 */
export function OverflowMenu({
  items,
  label = "More",
  text = false,
  wide = false,
  trigger = "dots",
}: {
  items: OverflowItem[];
  label?: string;
  /** A labelled button ("More actions ▾") instead of the ⋯ icon. */
  text?: boolean;
  /** The item card's 280px menu. */
  wide?: boolean;
  /** "caret": a bare `▾` trigger with no visible label -- `SplitButton`'s own
   *  menu half (Kraft-dkb6g), reusing this component's open/outside-click/
   *  keyboard-nav machinery instead of a second popover implementation. */
  trigger?: "dots" | "caret";
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState<OverflowItem | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const hintId = useId();

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
        button.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    // Capture, so Escape closes this menu before a surrounding surface's own
    // Escape (the board peek) closes everything.
    document.addEventListener("keydown", onKey, true);
    ref.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open, pending]);

  const onMenuKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const step = { ArrowDown: 1, ArrowUp: -1, Home: -Infinity, End: Infinity }[e.key];
    if (step === undefined) return;
    e.preventDefault();
    const rows = [...e.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]')];
    const at = rows.indexOf(document.activeElement as HTMLElement);
    const next = Math.abs(step) === Infinity ? (step < 0 ? 0 : rows.length - 1) : (at + step + rows.length) % rows.length;
    rows[next]?.focus();
  };

  return (
    <div className="overflow" ref={ref}>
      <button
        ref={button}
        className={
          trigger === "caret"
            ? "btn btn-ghost overflow-btn overflow-btn-caret"
            : text
              ? "btn btn-secondary overflow-text"
              : "btn btn-ghost overflow-btn"
        }
        aria-label={trigger === "caret" ? label || "more" : text ? undefined : label}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((v) => !v)}
      >
        {trigger === "caret" ? (
          <CaretDown size={12} />
        ) : text ? (
          <>
            {label}
            <CaretDown size={12} />
          </>
        ) : (
          <DotsThree size={16} />
        )}
      </button>
      {open && (
        <div className="overflow-menu" role="menu" data-wide={wide || undefined} onKeyDown={onMenuKey}>
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
            items.map((it, i) => (
              <Fragment key={it.label}>
                {it.divider && <div role="separator" className="overflow-sep" />}
                <button
                  role="menuitem"
                  data-danger={it.danger || undefined}
                  aria-disabled={it.disabled || undefined}
                  title={it.hint}
                  aria-describedby={it.hint ? `${hintId}-${i}` : undefined}
                  onClick={() => {
                    if (it.disabled) return;
                    if (it.confirm) {
                      setPending(it);
                      return;
                    }
                    setOpen(false);
                    it.onSelect();
                  }}
                >
                  {it.icon}
                  {it.label}
                </button>
                {it.hint && (
                  <span id={`${hintId}-${i}`} hidden>
                    {it.hint}
                  </span>
                )}
              </Fragment>
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
  /** A tooltip for the tab (hints are tooltips, never standing text). */
  hint?: string;
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
  // Roving tabindex (W6.8): one Tab stop for the strip; arrows move focus
  // between tabs, Enter/Space (a button's own activation) select.
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const step = { ArrowRight: 1, ArrowLeft: -1, Home: -Infinity, End: Infinity }[e.key];
    if (step === undefined) return;
    const btns = [...e.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
    const at = btns.indexOf(document.activeElement as HTMLButtonElement);
    const next = Math.abs(step) === Infinity ? (step < 0 ? 0 : btns.length - 1) : (at + step + btns.length) % btns.length;
    e.preventDefault();
    btns[next]?.focus();
  };
  const focusable = tabs.some((t) => t.id === value) ? value : tabs[0]?.id;
  return (
    <div className="tabs" role="tablist" onKeyDown={onKeyDown}>
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={t.id === value}
          tabIndex={t.id === focusable ? 0 : -1}
          className="tab"
          title={t.hint}
          onClick={() => onChange(t.id)}
        >
          {t.label}
          {t.count != null && <span className="tab-count">{" "}· {t.count}</span>}
        </button>
      ))}
    </div>
  );
}

/* — segmented control (design 28's Binding kind, screen 45's mobile settings
   header) — one active segment, not N independent toggle buttons. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  disabled,
  labelledBy,
}: {
  options: { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
  disabled?: boolean;
  /** id of the visible label naming the group (W7.1). */
  labelledBy?: string;
}) {
  return (
    <div className="segmented" role="group" aria-labelledby={labelledBy}>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          className="segmented-opt"
          aria-pressed={o.id === value}
          disabled={disabled}
          onClick={() => onChange(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* — switch (spec §4) —————————————————————————————————————————————————— */

/** 34×20 toggle, keyboard-operable via native `<button>`. Replaces the four
 *  hand-rolled `.switch` buttons `views/settings/` had (repos/plugins/
 *  intake/notify pages) — same markup, one definition. */
export function Switch({
  checked,
  onChange,
  label,
  disabled,
  title,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
  /** Shown on hover, and readable by assistive tech, when `disabled` — the
   *  reason it can't be flipped from here (design 28's "a subprocess has
   *  nothing to steer"). */
  title?: string;
}) {
  return (
    <button
      type="button"
      className="switch"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      title={title}
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
  trailing,
  dashed = false,
  overflow = false,
}: {
  label: ReactNode;
  count?: number;
  selected?: boolean;
  onClick?: (e: ReactMouseEvent<HTMLButtonElement>) => void;
  /** The "×" on a removable facet value (`template: default ×`) or the "▾"
   *  on the repo dropdown chip (design 04). */
  trailing?: ReactNode;
  /** The dashed "+ Filter" affordance chip (design 04). */
  dashed?: boolean;
  /** Wrapped past the facet bar's one row (W4.1): hidden there and out of
   *  the tab order, listed under "+N" instead. */
  overflow?: boolean;
}) {
  return (
    <button
      type="button"
      className="chip"
      data-dashed={dashed || undefined}
      data-overflow={overflow || undefined}
      tabIndex={overflow ? -1 : undefined}
      aria-pressed={selected}
      onClick={onClick}
    >
      {label}
      {count != null && <span className="chip-count">{count}</span>}
      {trailing}
    </button>
  );
}
