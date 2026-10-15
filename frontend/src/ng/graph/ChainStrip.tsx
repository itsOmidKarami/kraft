import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { ChainNode } from "./layout";
import { NodeGlyph } from "./NodeGlyph";
import { foldName, stripRow, type Fold, type RowEntry } from "./stages";
import { accessibleName, breakable } from "./types";
import { useRoving } from "./useRoving";
import "./graph.css";
import { tip } from "../ui/Tooltip";

/** Strip geometry: a node's column holds its glyph and its id under it; a folded run is one pill. */
export const S = { B: 30, CE: 92, CG: 92, CY: 22, PAD: 28, H: 80, PILL: 26, GAP: 16, MID: 120, MAX: 140 } as const;

/** A pill's width: its glyph, words and count, or, `compact`, its glyph and count alone. */
const foldW = (f: Fold, compact: boolean) => (compact ? 46 + String(f.nodes.length).length * 8 + (f.passed ? 14 : 0) : 58 + f.words.length * 7.4 + (f.passed ? 14 : 0)) + S.GAP;
/** A node's column: its id's width (bold, monospace) when that is more than the minimum, up to a cap past which it
 *  wraps, a lower one `compact`. */
const nodeW = (n: { id: string; kind: string }, compact = false) => Math.min(compact ? S.MID : S.MAX, Math.max(n.kind === "gate" ? S.CG : S.CE, Math.ceil(n.id.length * 8) + 12));
const foldKey = (key: string) => `f:${key}`;

export function stripLayout(row: RowEntry[], compact = false) {
  let x = S.PAD;
  const cols = row.map((entry) => {
    const w = entry.kind === "fold" ? foldW(entry, compact) : nodeW(entry, compact), cx = x + w / 2;
    x += w;
    return { entry, w, cx };
  });
  const half = (c: (typeof cols)[number]) => (c.entry.kind === "fold" ? (c.w - S.GAP) / 2 : c.entry.kind === "gate" ? 12 : S.B / 2);
  const edges = cols.slice(1).map((b, i) => {
    const a = cols[i];
    const todo = b.entry.kind === "fold" ? !b.entry.passed : b.entry.state === "todo";
    return { d: `M${a.cx + half(a) + 3} ${S.CY} L${b.cx - half(b) - 3} ${S.CY}`, todo };
  });
  return {
    items: cols.flatMap((c) => (c.entry.kind === "fold" ? [] : [{ node: c.entry, cx: c.cx, w: c.w }])),
    folds: cols.flatMap((c) => (c.entry.kind === "fold" ? [{ fold: c.entry, cx: c.cx, w: c.w }] : [])),
    edges,
    W: x + 2 * S.PAD,
    H: S.H,
  };
}

type Props = { nodes: ChainNode[]; viewing: string; onOpen?: (id: string) => void; onBack?: () => void };

/** The chain bar above a node view: the viewed node's run node by node, every id under its glyph, the other runs
 *  folded to a pill named by their gate (a click opens one in place). */
export function ChainStrip({ nodes, viewing, onOpen, onBack }: Props) {
  const [opened, setOpened] = useState<ReadonlySet<string>>(new Set());
  const row = useMemo(() => stripRow(nodes, opened, viewing), [nodes, opened, viewing]);
  // A strip wider than its bar folds its pills to their glyph and count, so no pill is cut at an edge.
  const bar = useRef<HTMLDivElement>(null);
  const [room, setRoom] = useState<number | null>(null);
  useEffect(() => {
    const el = bar.current;
    if (!el) return;
    const measure = () => setRoom(el.clientWidth || null);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const lay = useMemo(() => {
    const full = stripLayout(row);
    return room != null && full.W > room ? stripLayout(row, true) : full;
  }, [row, room]);
  const compact = lay.folds.some((f) => f.w < foldW(f.fold, false));
  const stops = row.map((e) => (e.kind === "fold" ? foldKey(e.key) : e.id));
  const roving = useRoving(viewing, stops[0]);
  // A strip wider than its bar scrolls to the node in view.
  const world = useRef<HTMLDivElement>(null);
  useEffect(() => {
    world.current?.querySelector("[aria-current]")?.scrollIntoView?.({ inline: "center", block: "nearest" });
  }, [viewing]);
  // A run opened in place hands focus to its first node, its pill having gone.
  const landing = useRef<string | null>(null);
  useEffect(() => {
    if (landing.current && stops.includes(landing.current)) roving.go(landing.current);
    landing.current = null;
  }, [row]); // eslint-disable-line react-hooks/exhaustive-deps
  const expand = (f: Fold) => {
    landing.current = f.nodes[0].id;
    setOpened((o) => new Set(o).add(f.key));
  };
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const i = stops.indexOf(roving.active ?? "");
    e.preventDefault();
    roving.go(stops[Math.max(0, Math.min(stops.length - 1, i + (e.key === "ArrowRight" ? 1 : -1)))]);
  };
  return (
    <div className="strip">
      <button type="button" className="strip-back" aria-label="Back to the chain" title="Back to the chain (Esc)" onClick={onBack}>← chain</button>
      <div ref={bar} className="strip-scroll">
        <div ref={world} role="group" aria-label="Chain" className="strip-world" style={{ width: lay.W, height: lay.H }} onKeyDown={onKeyDown}>
          <svg width={lay.W} height={lay.H} aria-hidden="true">
            {lay.edges.map((e, i) => <path key={i} d={e.d} className={e.todo ? "strip-edge is-todo" : "strip-edge"} />)}
          </svg>
          {lay.items.map(({ node: n, cx, w }) => {
            const v = n.id === viewing;
            return (
              <div key={n.id} className="strip-node" style={{ left: cx - w / 2, width: w, top: S.CY - S.B / 2 }}>
                <button
                  ref={roving.ref(n.id)}
                  type="button"
                  tabIndex={roving.tabIndex(n.id)}
                  className="strip-btn"
                  {...tip(accessibleName(n, n.kind === "gate" ? "gate" : "node"), n.id)}
                  aria-current={v ? "step" : undefined}
                  onFocus={() => roving.go(n.id)}
                  onClick={() => onOpen?.(n.id)}
                >
                  <NodeGlyph {...n} size="sm" sel={v} prob={n.prob && !v} />
                </button>
                <span className={v ? "strip-label is-viewing" : "strip-label"} style={{ maxWidth: w - 6 }}>{breakable(n.id)}</span>
              </div>
            );
          })}
          {lay.folds.map(({ fold: f, cx, w }) => {
            const key = foldKey(f.key);
            const gate = f.nodes.at(-1)!;
            return (
              <button
                key={key}
                ref={roving.ref(key)}
                type="button"
                tabIndex={roving.tabIndex(key)}
                {...tip(foldName(f), f.nodes.map((n) => n.id).join(" → "))}
                className="strip-fold"
                style={{ left: cx - (w - S.GAP) / 2, top: S.CY - S.PILL / 2, width: w - S.GAP, height: S.PILL }}
                onFocus={() => roving.go(key)}
                onClick={() => expand(f)}
              >
                {gate.kind === "gate" && <NodeGlyph kind="gate" state={f.passed ? "done" : undefined} size="sm" />}
                {!compact && <span>{f.words}</span>}
                {f.passed && <span className="fold-done" aria-hidden="true">✓</span>}
                <span className="strip-fold-n">{f.nodes.length}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
