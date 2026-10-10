import { memo, useEffect, useMemo, useRef, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import type { CompareFile } from "../../types";
import { ChevronDown, ChevronUp, ChevronsUpDown, EllipsisVertical, MessageSquare, Plus } from "../icons";
import { IconButton } from "../ui/IconButton";
import { Menu } from "../ui/Menu";
import { showToast } from "../ui/Toast";
import { gapsOf, STEP, type Gap, type Grow } from "./expand";
import type { PatchFile } from "./patch";
import type { DiffPrefs } from "./prefs";
import { inRange, lineIndex, rangeBetween, rangeLabel, startSideOf, toward, type LineIndex, type LineRange } from "./range";
import { anchorsOf, buildRows, spans, type Anchor, type Cell, type Row, type Side } from "./rows";
import { languageOf } from "./tokenize";
import { tip } from "../ui/Tooltip";

/** A picked range of one file's lines. `anchor` is where the pick started,
 *  `head` where it ends now; Shift moves only the head. Both are on `side`,
 *  unless `anchorSide` puts the anchor on the other one: a pick across sides. */
export interface Pick {
  path: string;
  side: Side;
  anchor: number;
  head: number;
  anchorSide?: Side;
}
export const pickRange = (p: Pick) => [Math.min(p.anchor, p.head), Math.max(p.anchor, p.head)] as const;
export const anchorOf = (p: Pick): Anchor => ({ side: p.anchorSide ?? p.side, line: p.anchor });
export const headOf = (p: Pick): Anchor => ({ side: p.side, line: p.head });
/** The pick from `anchor` to `head`. */
export const pickOf = (path: string, anchor: Anchor, head: Anchor): Pick => ({
  path,
  side: head.side,
  anchor: anchor.line,
  head: head.line,
  ...(anchor.side !== head.side && { anchorSide: anchor.side }),
});
/** The lines a pick covers, start first. */
export const rangeOfPick = (p: Pick, ix: LineIndex): LineRange => rangeBetween(anchorOf(p), headOf(p), ix);

export interface DiffViewProps {
  /** The files to draw, in the comparison's order. */
  files: CompareFile[];
  patch: Map<string, PatchFile>;
  prefs: DiffPrefs;
  collapsed: Set<string>;
  onCollapse: (path: string, collapsed: boolean) => void;
  /** One-file mode shows only this one. */
  selected: string | null;
  isViewed: (path: string) => boolean;
  onViewed: (path: string, viewed: boolean) => void;
  /** Open threads on a file, for its collapsed header. */
  threadCount: (path: string) => number;
  picked: Pick | null;
  onPick: (p: Pick | null) => void;
  /** The pick's own range edited (a handle dragged, Shift-click, Shift+arrows): `onPick` when absent. The page moves an open composer with it. */
  onRange?: (p: Pick) => void;
  /** Enter, or `c`, on a pick: open the composer on it. */
  onCompose: (p: Pick) => void;
  /** Comment on this file: the composer at the file's top. */
  onFileComment: (path: string) => void;
  /** What sits under a line (threads, the composer), and at a file's top. */
  after?: (path: string, a: Anchor) => ReactNode;
  /** The ranges of a file's open threads, shaded in the diff. */
  commented?: (path: string) => LineRange[];
  top?: (path: string) => ReactNode;
  /** Set when the server cut the diff: its byte cap and how many files it left out. */
  truncated: { bytes: number; files: number } | null;
  /** An ended item takes no comment (the server refuses it): no line picks, no file comment button. */
  readOnly?: boolean;
  /** Show unchanged lines in the gap before hunk `gap` of a file (after its last hunk, at the hunk count). Absent: no arrows. */
  onExpand?: (path: string, gap: number, how: Grow) => void;
}

/** The diff column's content (prototype 419–492). The split view always
 *  wraps: each half is half the column, so a long line cannot push the new
 *  side out of view. */
export function DiffView(p: DiffViewProps) {
  const shown = p.prefs.one_file_at_a_time ? p.files.filter((f) => f.path === (p.selected ?? p.files[0]?.path)) : p.files;
  const col = useRef<HTMLDivElement>(null);
  // Choosing a file in the tree brings it to the top of the column.
  useEffect(() => {
    if (!p.selected || p.prefs.one_file_at_a_time) return;
    col.current?.querySelector(`[data-file="${CSS.escape(p.selected)}"]`)?.scrollIntoView?.({ block: "start" });
  }, [p.selected, p.prefs.one_file_at_a_time]);
  if (!p.files.length) return <p className="rv-empty">No files match this comparison.</p>;
  return (
    <div ref={col} className={`rv-files${p.prefs.wrap_lines || p.prefs.layout === "split" ? " is-wrap" : ""}`} data-colours={p.prefs.colours}>
      {shown.map((f) => {
        const pf = p.patch.get(f.path);
        return pf ? <FileBlock key={f.path} file={f} pf={pf} {...p} /> : null;
      })}
      {p.truncated && (
        <p className="rv-empty">
          The diff stops at {Math.round(p.truncated.bytes / 1024)} KB ({p.truncated.files} {p.truncated.files === 1 ? "file" : "files"} not shown). The rest is in the worktree.
        </p>
      )}
    </div>
  );
}

function note(f: CompareFile, pf: PatchFile) {
  const by = f.touched_by.length ? `by ${f.touched_by.join(" and ")}` : "";
  if (pf.status === "added") return by ? `new file, ${by}` : "new file";
  if (pf.status === "deleted") return by ? `deleted, ${by}` : "deleted";
  if (pf.status === "renamed") return `renamed from ${pf.oldPath}${by ? `, ${by}` : ""}`;
  return by;
}

const copy = (text: string, what: string) =>
  navigator.clipboard?.writeText(text).then(
    () => showToast(`Copied ${what}`),
    () => showToast(`Could not copy the ${what}`),
  );

function FileBlock({ file, pf, ...p }: DiffViewProps & { file: CompareFile; pf: PatchFile }) {
  const lang = languageOf(file.path);
  const rows = useMemo(() => buildRows(pf, p.prefs.layout, lang, p.prefs.word_highlight), [pf, p.prefs.layout, lang, p.prefs.word_highlight]);
  const collapsed = p.collapsed.has(file.path);
  const viewed = p.isViewed(file.path);
  const threads = p.threadCount(file.path);
  const picked = p.picked?.path === file.path ? p.picked : null;
  const index = useMemo(() => lineIndex(pf), [pf]);
  const range = useMemo(() => picked && rangeOfPick(picked, index), [picked, index]);
  const isPicked = (a: Anchor) => !!range && inRange(range, a, index);
  const commented = p.commented?.(file.path) ?? NO_RANGES;
  // An added or a deleted file is all in its diff: nothing more to show.
  const canExpand = !!p.onExpand && pf.status !== "added" && pf.status !== "deleted";
  const gaps = useMemo(() => gapsOf(pf), [pf]);
  // An arrow that showed the last of its lines is gone, and the focus with it: the line group takes it.
  // Only after an arrow: lines drawn for a thread as the page loads take no focus.
  const group = useRef<HTMLDivElement>(null);
  const pressed = useRef(false);
  useEffect(() => {
    if (pressed.current && document.activeElement === document.body) group.current?.focus({ preventScroll: true });
    pressed.current = false;
  }, [pf]);
  const lines = rows.filter((r): r is Exclude<Row, { t: "hunk" }> => r.t !== "hunk");
  const { anchors, hunkOf } = useMemo(() => {
    let h = -1;
    return { anchors: rows.map(anchorsOf), hunkOf: rows.map((r) => (r.t === "hunk" ? ++h : h)) };
  }, [rows]);
  // Each row's anchors in the pick and in a thread's range, as a string the memoized row compares.
  const keys = (as: Anchor[], hit: (a: Anchor) => boolean) => as.filter(hit).map((a) => `|${a.side}${a.line}|`).join("");
  const commentedKeys = useMemo(() => anchors.map((as) => keys(as, (a) => commented.some((r) => inRange(r, a, index)))), [anchors, commented, index]);

  const pick = (a: Anchor, extend: boolean) => !p.readOnly && (extend && picked ? (p.onRange ?? p.onPick)(pickOf(file.path, anchorOf(picked), a)) : p.onPick(pickOf(file.path, a, a)));

  // The gutter's handlers are made once per file and read the current render
  // through `live`, so a pick re-renders only the rows it touches (RowView is memoized).
  const live = useRef({ p, picked, isPicked, pick, index });
  live.current = { p, picked, isPicked, pick, index };
  const gutter = useMemo<Gutter>(() => {
    const path = file.path;
    // Enter and `c` are read off the line group, so a click in the gutter hands focus back to it.
    const refocus = (e: MouseEvent) => e.currentTarget.closest<HTMLElement>(".rv-lines")?.focus({ preventScroll: true });
    const rowOf = (el: EventTarget | null) => (el instanceof Element ? el.closest<HTMLElement>("[data-hunk]") : null);
    // Shift extends the pick to the line pressed: a context line on the pick's side (as the arrows do),
    // a line on the other side only across sides, as a removed line and its replacement.
    const extendTo = (a: Anchor, e: MouseEvent): Anchor => {
      const { picked, index } = live.current;
      return e.shiftKey && picked ? toward(a, anchorOf(picked), index) : a;
    };
    // + comments on the pick when its line is in it, else on its own line.
    const plus = (a: Anchor) => {
      const { p, picked, isPicked } = live.current;
      if (picked && isPicked(a)) return p.onCompose(picked);
      const one = pickOf(path, a, a);
      p.onPick(one);
      p.onCompose(one);
    };
    // A press in the gutter starts a drag: the pick follows the pointer over the
    // lines of its hunk, at most once a frame, and a drag that began on + opens
    // the composer on the range when the button comes up.
    let frame = 0;
    const emit = () => {
      frame = 0;
      const d = drag.current;
      if (!d?.moved) return;
      const next = pickOf(path, d.anchor, d.head);
      // A handle (or a Shift-press) edits the range it started from: the page moves an open composer with it.
      if (d.edit) (live.current.p.onRange ?? live.current.p.onPick)(next);
      else live.current.p.onPick(next);
    };
    const startDrag = (at: Anchor, isPlus: boolean) => (e: MouseEvent) => {
      const { p, picked, isPicked } = live.current;
      if (p.readOnly || e.button !== 0) return;
      // No text selection while dragging, and the focus goes to the line group, not the button.
      e.preventDefault();
      refocus(e);
      const a = extendTo(at, e);
      // + on a line of the pick keeps the pick until the pointer moves: a click there comments on all of it.
      const keep = isPlus && isPicked(at) && !e.shiftKey;
      const anchor = e.shiftKey && picked ? anchorOf(picked) : a;
      const button = e.currentTarget;
      const edit = e.shiftKey && !!picked;
      drag.current = { anchor: keep ? a : anchor, head: a, from: a, hunk: rowOf(button)?.dataset.hunk, moved: false, edit };
      if (!keep) (edit ? p.onRange ?? p.onPick : p.onPick)(pickOf(path, anchor, a));
      window.addEventListener(
        "mouseup",
        (up) => {
          cancelAnimationFrame(frame);
          emit();
          const d = drag.current;
          drag.current = null;
          if (!d || !isPlus) return;
          if (!same(d.head, d.from)) live.current.p.onCompose(pickOf(path, d.anchor, d.head));
          // Let go on the row it started on: the same as a click on its + (a release on the + itself is that click).
          else if (!(up.target instanceof Node && button.contains(up.target))) plus(at);
        },
        { once: true },
      );
    };
    // A handle on the shaded range's first or last row: a press takes that end, the other stays, and the
    // pointer moves it over the lines of its hunk like a drag does. Letting go leaves the range as it is.
    const startGrip = (end: "start" | "last") => (e: MouseEvent) => {
      const { p, picked, index } = live.current;
      if (p.readOnly || e.button !== 0 || !picked) return;
      e.preventDefault();
      e.stopPropagation();
      refocus(e);
      const r = rangeOfPick(picked, index);
      const first: Anchor = { side: startSideOf(r), line: r.start };
      const last: Anchor = { side: r.side, line: r.end };
      const [held, fixed] = end === "start" ? [first, last] : [last, first];
      drag.current = { anchor: fixed, head: held, from: held, hunk: rowOf(e.currentTarget)?.dataset.hunk, moved: false, edit: true };
      window.addEventListener(
        "mouseup",
        () => {
          cancelAnimationFrame(frame);
          emit();
          drag.current = null;
        },
        { once: true },
      );
    };
    // The line under the pointer shows its +: one attribute moved on mouseover, not a
    // `:hover` rule every row of a huge diff answers to on each crossing (R9b-06). An
    // attribute, not a class, so a row React draws again keeps it.
    let hovered: HTMLElement | null = null;
    const hover = (line: HTMLElement | null) => {
      if (line === hovered) return;
      hovered?.removeAttribute("data-hover");
      line?.setAttribute("data-hover", "");
      hovered = line;
    };
    const onOver = (e: MouseEvent) => {
      hover(e.target instanceof Element ? e.target.closest<HTMLElement>(".rv-half, .rv-row:not(.is-split)") : null);
      const d = drag.current;
      const row = rowOf(e.target);
      if (!d || !row || row.dataset.hunk !== d.hunk) return;
      // The line under the pointer: a split half's own, else the row's. A context line
      // is read on the drag's side; a line on the other side only makes a range across sides.
      const half = e.target instanceof Element ? e.target.closest<HTMLElement>(".rv-half[data-line]") : null;
      const at: Anchor | null = half
        ? { side: half.dataset.side as Side, line: Number(half.dataset.line) }
        : row.dataset.new ? { side: "new", line: Number(row.dataset.new) } : row.dataset.old ? { side: "old", line: Number(row.dataset.old) } : null;
      if (!at) return;
      const head = toward(at, d.anchor, live.current.index);
      if (same(head, d.head)) return;
      d.head = head;
      d.moved = true;
      if (!frame) frame = requestAnimationFrame(emit);
    };
    const onExpand = (gap: number, how: Grow) => () => {
      pressed.current = true;
      live.current.p.onExpand?.(path, gap, how);
    };
    return { onClick: (a) => (e) => (live.current.pick(extendTo(a, e), e.shiftKey), refocus(e)), startDrag, startGrip, onPlus: (a) => () => plus(a), onOver, onLeave: () => hover(null), onExpand };
  }, [file.path]);
  const drag = useRef<{ anchor: Anchor; head: Anchor; from: Anchor; hunk: string | undefined; moved: boolean; edit: boolean } | null>(null);

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    // Only the line area's own keys: a thread or the composer sits inside it, and its typing is its own.
    if (e.target !== e.currentTarget) return;
    if (e.key === "n" || e.key === "p") {
      const all = [...document.querySelectorAll<HTMLElement>(".rv-lines")];
      all[all.indexOf(e.currentTarget) + (e.key === "n" ? 1 : -1)]?.focus();
      return e.preventDefault();
    }
    if ((e.key === "Enter" || e.key === "c") && picked) {
      e.preventDefault();
      return p.onCompose(picked);
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    if (!lines.length) return;
    const on = (r: Row, side: Side) => anchorsOf(r).find((a) => a.side === side);
    const at = picked ? lines.findIndex((r) => on(r, picked.side)?.line === picked.head) : -1;
    const step = e.key === "ArrowDown" ? 1 : -1;
    let i = at < 0 ? 0 : at + step;
    // Shift extends on the pick's side: a context line counts there, the other side's lines are skipped.
    if (e.shiftKey && picked) {
      while (lines[i] && !on(lines[i], picked.side)) i += step;
      if (lines[i]) pick(on(lines[i], picked.side)!, true);
      return;
    }
    if (lines[i]) pick(lines[i].at, false);
  };

  return (
    <section className="rv-file" data-file={file.path} aria-label={file.path}>
      <header className="rv-file-head">
        <button type="button" className="rv-fold" aria-expanded={!collapsed} {...tip(collapsed ? `Expand ${file.path}` : `Collapse ${file.path}`)} onClick={() => p.onCollapse(file.path, !collapsed)}>
          {collapsed ? "▸" : "▾"}
        </button>
        <span className="rv-file-path rv-mono" title={file.path} data-allow-ellipsis="">{file.path}</span>
        <span className="rv-add">+{file.insertions}</span>
        <span className="rv-del">−{file.deletions}</span>
        <span className="rv-file-note">{note(file, pf)}</span>
        {collapsed && threads > 0 && <span className="rv-file-threads">{threads} {threads === 1 ? "thread" : "threads"}</span>}
        <span className="rv-spacer" />
        {!p.readOnly && <IconButton label="Comment on this file" onClick={() => p.onFileComment(file.path)}><MessageSquare size={15} aria-hidden /></IconButton>}
        <button type="button" className="rv-viewed-btn" aria-pressed={viewed} onClick={() => p.onViewed(file.path, !viewed)}>
          <span aria-hidden="true">{viewed ? "☑" : "☐"}</span> Viewed
        </button>
        <Menu
          label={`File menu for ${file.path}`}
          trigger={<EllipsisVertical size={15} aria-hidden />}
          items={[
            { label: "Copy diff", onSelect: () => void copy(pf.text, "diff") },
            { label: "Copy path", onSelect: () => void copy(file.path, "path") },
          ]}
        />
      </header>
      {collapsed ? (
        <button type="button" className="rv-collapsed" onClick={() => p.onCollapse(file.path, false)}>collapsed, click to expand</button>
      ) : (
        <div className="rv-body-file">
          {p.top?.(file.path)}
          {pf.binary ? (
            <p className="rv-file-msg">Binary file, not shown</p>
          ) : !pf.hunks.length ? (
            <p className="rv-file-msg">{pf.status === "renamed" ? "Renamed with no changes" : "No changes to show"}</p>
          ) : (
            <div ref={group} className={`rv-lines is-${p.prefs.layout}`} tabIndex={0} role="group" aria-label={`Lines of ${file.path}: arrows pick a line, Shift and the arrows move the end of the range, Enter comments, n and p change file`} onKeyDown={onKey} onMouseOver={gutter.onOver} onMouseLeave={gutter.onLeave}>
              {rows.map((r, i) => (
                <RowView
                  key={i}
                  row={r}
                  hunk={hunkOf[i]}
                  gap={r.t === "hunk" && canExpand ? gaps[hunkOf[i]] : null}
                  // Only the rows the pick touches see it, so the rest skip the render.
                  pick={picked && anchors[i].some(isPicked) ? picked : null}
                  range={picked && anchors[i].some(isPicked) ? range : null}
                  picked={picked ? keys(anchors[i], isPicked) : ""}
                  commented={commentedKeys[i]}
                  readOnly={!!p.readOnly}
                  g={gutter}
                  slots={p.after ? anchors[i].map((a) => p.after!(file.path, a)) : NONE}
                />
              ))}
              {canExpand && gaps[pf.hunks.length].count !== 0 && <div className="rv-hunk"><Expander at={pf.hunks.length} gap={gaps[pf.hunks.length]} tail g={gutter} /></div>}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

const MARK = { "+": "+", "-": "−", " ": " " } as const;

function Code({ cell }: { cell: Cell }) {
  return (
    <code className="rv-code">
      {spans(cell).map((s, i) => (
        <span key={i} className={`${s.cls ? `tok-${s.cls}` : ""}${s.changed ? " rv-word-change" : ""}` || undefined}>{s.text}</span>
      ))}
    </code>
  );
}

/** A file's gutter handlers, made once per file. */
interface Gutter {
  onClick: (a: Anchor) => (e: MouseEvent) => void;
  startDrag: (a: Anchor, plus: boolean) => (e: MouseEvent) => void;
  startGrip: (end: "start" | "last") => (e: MouseEvent) => void;
  onPlus: (a: Anchor) => () => void;
  onOver: (e: MouseEvent) => void;
  onLeave: () => void;
  onExpand: (gap: number, how: Grow) => () => void;
}

/** The arrows on a hunk's row, for the unchanged lines before it (gap `at`); `tail` is the row after the last hunk,
 *  whose count is null until the file was read. A short gap has only the arrow that shows it all. Each names its
 *  lines, so no two in a file read the same. */
function Expander({ at, gap: { from, count }, tail, g }: { at: number; gap: Gap; tail?: boolean; g: Gutter }) {
  const many = count === null || count > STEP;
  const all = count === null ? "Show the rest of the file" : count === 1 ? `Show line ${from}` : `Show lines ${from}–${from + count - 1}`;
  return (
    <span className="rv-expand">
      {many && at > 0 && <IconButton label={`Show ${STEP} more lines below line ${from - 1}`} onClick={g.onExpand(at, "down")}><ChevronDown size={13} aria-hidden /></IconButton>}
      {many && !tail && <IconButton label={`Show ${STEP} more lines above line ${from + (count ?? 0)}`} onClick={g.onExpand(at, "up")}><ChevronUp size={13} aria-hidden /></IconButton>}
      <IconButton label={all} onClick={g.onExpand(at, "all")}><ChevronsUpDown size={13} aria-hidden /></IconButton>
    </span>
  );
}

const NONE: ReactNode[] = [];
const NO_RANGES: LineRange[] = [];
const same = (a: Anchor, b: Anchor) => a.side === b.side && a.line === b.line;
/** Whether `keys` (a row's `|old5|`-style anchors) holds `a`. */
const has = (keys: string, a: Anchor) => keys.includes(`|${a.side}${a.line}|`);

/** A line number: a press picks its line on its own side, Shift extends, a drag sweeps a range.
 *  An empty one (an added line's old number) picks the row's line. */
function Num({ shown, a, g }: { shown: number | null; a: Anchor; g: Gutter }) {
  return (
    <button type="button" tabIndex={-1} className="rv-num" aria-label={shown === null ? undefined : `Pick ${a.side} line ${a.line}`} aria-hidden={shown === null || undefined} onMouseDown={g.startDrag(a, false)} onClick={g.onClick(a)}>
      {shown ?? ""}
    </button>
  );
}

/** The + that opens the composer, in its own lane left of the numbers and shown
 *  on hover. Only the pick's last line keeps it in the tab order: one stop per file, not one per line. */
function PlusButton({ a, pick, range, inPick, g }: { a: Anchor; pick: Pick | null; range: LineRange | null; inPick: boolean; g: Gutter }) {
  const head = !!pick && pick.side === a.side && pick.head === a.line;
  const name = rangeLabel(range && inPick ? range : { side: a.side, start: a.line, end: a.line });
  const what = `Comment on ${name[0].toLowerCase()}${name.slice(1)}`;
  return (
    <button type="button" tabIndex={head ? 0 : -1} className={`rv-plus${head ? " is-head" : ""}`} {...tip(what)} onMouseDown={g.startDrag(a, true)} onClick={g.onPlus(a)}>
      <Plus size={12} aria-hidden />
    </button>
  );
}

/** The small handle on the shaded range's first or last row, dragged to move that end. A mouse's: the keyboard has Shift and the arrows. */
function Grip({ end, g }: { end: "start" | "last"; g: Gutter }) {
  return <span className={`rv-grip is-${end}`} data-tip={end === "start" ? "Drag to change the first line" : "Drag to change the last line"} onMouseDown={g.startGrip(end)} />;
}

/** Whether the row holding `as` is where the range `r` starts (`first`) or ends (`last`). */
const endsOf = (r: LineRange | null, as: Anchor[]) => ({
  first: !!r && as.some((a) => same(a, { side: startSideOf(r), line: r.start })),
  last: !!r && as.some((a) => same(a, { side: r.side, line: r.end })),
});

interface RowProps {
  row: Row;
  hunk: number;
  /** A hunk row's unchanged lines before it, when arrows can show them. */
  gap: Gap | null;
  /** The pick and its range, when it covers a line of this row; else null. */
  pick: Pick | null;
  range: LineRange | null;
  /** The row's anchors in the pick, and in a thread's range (`has`). */
  picked: string;
  commented: string;
  readOnly: boolean;
  g: Gutter;
  /** What sits under each of the row's anchors (`anchorsOf`). */
  slots: ReactNode[];
}

const RowView = memo(function RowView({ row, hunk, gap, pick, range, picked, commented, readOnly, g, slots }: RowProps) {
  if (row.t === "hunk") return <div className="rv-hunk rv-mono">{gap && (gap.count ?? 0) > 0 && <Expander at={hunk} gap={gap} g={g} />}{row.text}</div>;
  const kind = (c: Cell | null) => (c ? (c.kind === "+" ? " is-add" : c.kind === "-" ? " is-del" : "") : " is-none");
  const marks = (as: Anchor[]) => `${as.some((a) => has(picked, a)) ? " is-picked" : ""}${as.some((a) => has(commented, a)) ? " is-commented" : ""}`;
  const anchors = anchorsOf(row);
  const tail = slots.length > 0 && anchors.map((a, i) => <AfterSlot key={`${a.side}${a.line}`} node={slots[i]} />);
  if (row.t === "u") {
    const c = row.cell;
    // A context line is on both sides: each number picks its own, and + follows a pick on either.
    const plusAt = anchors.find((a) => has(picked, a)) ?? row.at;
    const ends = endsOf(range, anchors);
    return (
      <>
        <div className={`rv-row${kind(c)}${marks(anchors)}`} data-hunk={hunk} data-old={c.old ?? undefined} data-new={c.new ?? undefined}>
          <Num shown={c.old} a={c.old === null ? row.at : { side: "old", line: c.old }} g={g} />
          <Num shown={c.new} a={c.new === null ? row.at : { side: "new", line: c.new }} g={g} />
          {!readOnly && <PlusButton a={plusAt} pick={pick} range={range} inPick={has(picked, plusAt)} g={g} />}
          {!readOnly && ends.first && <Grip end="start" g={g} />}
          {!readOnly && ends.last && <Grip end="last" g={g} />}
          <span className="rv-mark" aria-hidden="true">{MARK[c.kind]}</span>
          <Code cell={c} />
        </div>
        {tail}
      </>
    );
  }
  const half = (c: Cell | null, side: Side) => {
    const a: Anchor | null = c ? { side, line: side === "old" ? c.old! : c.new! } : null;
    const ends = endsOf(range, a ? [a] : []);
    return (
      <div className={`rv-half${kind(c)}${a ? marks([a]) : ""}`} data-side={a?.side} data-line={a?.line}>
        {a ? <Num shown={a.line} a={a} g={g} /> : <span className="rv-num" />}
        {a && !readOnly && <PlusButton a={a} pick={pick} range={range} inPick={has(picked, a)} g={g} />}
        {a && !readOnly && ends.first && <Grip end="start" g={g} />}
        {a && !readOnly && ends.last && <Grip end="last" g={g} />}
        <span className="rv-mark" aria-hidden="true">{c ? MARK[c.kind] : ""}</span>
        {c ? <Code cell={c} /> : <code className="rv-code" />}
      </div>
    );
  };
  return (
    <>
      <div className="rv-row is-split" data-hunk={hunk} data-old={row.left?.old ?? undefined} data-new={row.right?.new ?? undefined}>
        {half(row.left, "old")}
        {half(row.right, "new")}
      </div>
      {tail}
    </>
  );
}, (a, b) => a.row === b.row && a.hunk === b.hunk && a.gap === b.gap && a.pick === b.pick && a.range === b.range && a.picked === b.picked && a.commented === b.commented && a.readOnly === b.readOnly && a.g === b.g && a.slots.length === b.slots.length && a.slots.every((n, i) => n === b.slots[i]));

const AfterSlot = ({ node }: { node: ReactNode }) => (node ? <div className="rv-after">{node}</div> : null);
