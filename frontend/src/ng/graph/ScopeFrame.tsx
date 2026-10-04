import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { rowHeight, type Chip, type ScopesView } from "../item/scopeView";
import "./graph.css";

type Rove = { ref: (key: string) => (el: HTMLElement | null) => void; tabIndex: (key: string) => number; go: (key: string | undefined) => void; onFocus: (key: string, el: HTMLElement) => void };

export const chipKey = (step: string, task: string, scope: string) => `c:${step}/${task}/${scope}`;

const WORD: Record<Chip["state"], string> = { done: "done", failed: "failed", running: "running", waiting: "waiting", skipped: "not picked" };

type Props = {
  view: ScopesView;
  /** The frame's full width (`frameWidth`, never narrowing while it is open). */
  width: number;
  step: string;
  task: string;
  rect: { x: number; y: number; w: number; h: number };
  /** Rows in: the frame is open and its contents show. */
  on: boolean;
  /** The frame is at its full size; before that it is the task's box. */
  full: boolean;
  /** Fading away at the end of a close. */
  out: boolean;
  selectedScope?: string;
  taskKey: string;
  rove: Rove;
  onTask: () => void;
  onScope: (key: string) => void;
  onClose: () => void;
};

/** What a chip's tooltip says: the whole command and the paths it covers, then why it is dashed or tagged. */
const tooltip = (c: Chip) => [c.command, c.paths && `covers ${c.paths}`, c.state === "skipped" ? "Ran last round; no changed path reaches it this round" : c.fresh ? "Picked for the first time this round" : ""].filter(Boolean).join("\n");

/** A repository's chips in one row. The frame is as wide as its longest row (`frameWidth`); should a row still run
 *  past it, the row scrolls, and its right edge fades over 24px to say there is more. */
function ChipRow({ fork, children }: { fork: boolean; children: ReactNode }) {
  const el = useRef<HTMLDivElement>(null);
  const [more, setMore] = useState(false);
  useEffect(() => {
    const node = el.current;
    if (!node || fork) return;
    const look = () => setMore(node.scrollWidth - node.clientWidth - node.scrollLeft > 1);
    look();
    node.addEventListener("scroll", look);
    const ro = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(look);
    ro?.observe(node);
    return () => { node.removeEventListener("scroll", look); ro?.disconnect(); };
  });
  return <div ref={el} className={`scope-chips${fork ? " is-fork" : ""}${more ? " is-more" : ""}`}>{children}</div>;
}

/** The changed-test-scope task open: a frame with one row per repository, in the order the task visits them, and
 *  each repository's scopes as chips, joined in a line when they run one after another, forked when together. */
export function ScopeFrame({ view, width, step, task, rect, on, full, out, selectedScope, taskKey, rove, onTask, onScope, onClose }: Props) {
  // A click on the frame's own background steps back from a scope to the task; the canvas's closes the frame.
  const background = (e: MouseEvent) => {
    if (selectedScope && !(e.target as Element).closest("button")) onTask();
  };
  const parallel = view.execution === "parallel";
  return (
    <div role="group" aria-label={`${task}, repositories and scopes`} className={`scope-frame${full ? " is-full" : ""}${out ? " is-out" : ""}${on ? " is-on" : ""}`} style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }} onClick={background}>
      {/* The contents keep the frame's full width while it grows from the box, so nothing reflows on the way. */}
      <div className="scope-inner" style={{ width }}>
        <div className="scope-head">
          <button ref={rove.ref(taskKey)} type="button" tabIndex={rove.tabIndex(taskKey)} className="scope-task" aria-pressed={!selectedScope} onFocus={(e) => rove.onFocus(taskKey, e.currentTarget)} onClick={onTask}>{task}</button>
          <span className="scope-sub">round {view.round} · repos in order · scopes {parallel ? "in parallel" : "in order"}</span>
          {/* Out of the Tab order, which the canvas keeps to one stop: Esc closes it from the keys. */}
          <button type="button" tabIndex={-1} className="scope-close" onClick={onClose}>close ✕</button>
        </div>
        <div className="scope-body">
          <span className="scope-rail" aria-hidden="true" />
          {view.rows.map((r, i) => (
            <div key={r.id ?? "·"} className={`scope-row${parallel ? " is-fork" : ""}`} style={{ minHeight: rowHeight(r, view.execution) }}>
              <span className={`scope-ring is-${r.state}`} aria-hidden="true">{i + 1}</span>
              <div className="scope-repo">
                <span className={`scope-name${r.state === "unreached" ? " is-off" : ""}`}>{r.name}</span>
                <span className={`scope-note is-${r.state}`}>{r.note}</span>
              </div>
              <ChipRow fork={parallel}>
                {r.chips.map((c, j) => {
                  const key = chipKey(step, task, c.key);
                  return (
                    <div key={c.key} className="scope-item">
                      {parallel && <span className="scope-tick" aria-hidden="true" />}
                      {!parallel && j > 0 && <span className="scope-arrow" aria-hidden="true">→</span>}
                      <button
                        ref={rove.ref(key)}
                        type="button"
                        tabIndex={rove.tabIndex(key)}
                        aria-label={[c.name, WORD[c.state], c.fresh && "new this round"].filter(Boolean).join(", ")}
                        aria-pressed={selectedScope === c.key}
                        title={tooltip(c)}
                        className={`scope-chip is-${c.state}${selectedScope === c.key ? " is-sel" : ""}`}
                        onFocus={(e) => rove.onFocus(key, e.currentTarget)}
                        onClick={() => onScope(c.key)}
                      >
                        <span className="scope-dot" aria-hidden="true" />
                        <span className="scope-chip-name">{c.name}</span>
                        {c.meta && <span className="scope-meta">{c.meta}</span>}
                        {c.fresh && <span className="scope-new" title="Picked for the first time this round">new</span>}
                      </button>
                    </div>
                  );
                })}
              </ChipRow>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
