import { memo, useEffect, useMemo, useRef, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import type { CompareFile } from "../../types";
import { EllipsisVertical, MessageSquare, Plus } from "../icons";
import { IconButton } from "../ui/IconButton";
import { Menu } from "../ui/Menu";
import { showToast } from "../ui/Toast";
import type { PatchFile } from "./patch";
import type { DiffPrefs } from "./prefs";
import { anchorsOf, buildRows, spans, type Anchor, type Cell, type Row, type Side } from "./rows";
import { rangeName } from "./Thread";
import { languageOf } from "./tokenize";

/** A picked range of lines on one side of one file. `anchor` is where the
 *  pick started, `head` where it ends now; Shift moves only the head. */
export interface Pick {
  path: string;
  side: Side;
  anchor: number;
  head: number;
}
export const pickRange = (p: Pick) => [Math.min(p.anchor, p.head), Math.max(p.anchor, p.head)] as const;

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
  /** Enter, or `c`, on a pick: open the composer on it. */
  onCompose: (p: Pick) => void;
  /** Comment on this file: the composer at the file's top. */
  onFileComment: (path: string) => void;
  /** What sits under a line (threads, the composer), and at a file's top. */
  after?: (path: string, a: Anchor) => ReactNode;
  top?: (path: string) => ReactNode;
  /** Set when the server cut the diff: its byte cap and how many files it left out. */
  truncated: { bytes: number; files: number } | null;
  /** An ended item takes no comment (the server refuses it): no line picks, no file comment button. */
  readOnly?: boolean;
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
  const [lo, hi] = picked ? pickRange(picked) : [0, -1];
  const isPicked = (a: Anchor) => !!picked && a.side === picked.side && a.line >= lo && a.line <= hi;
  const lines = rows.filter((r): r is Exclude<Row, { t: "hunk" }> => r.t !== "hunk");
  const { anchors, hunkOf } = useMemo(() => {
    let h = -1;
    return { anchors: rows.map(anchorsOf), hunkOf: rows.map((r) => (r.t === "hunk" ? ++h : h)) };
  }, [rows]);

  const pick = (a: Anchor, extend: boolean) =>
    !p.readOnly &&
    p.onPick(extend && picked && picked.side === a.side ? { ...picked, head: a.line } : { path: file.path, side: a.side, anchor: a.line, head: a.line });

  // The gutter's handlers are made once per file and read the current render
  // through `live`, so a pick re-renders only the rows it touches (RowView is memoized).
  const live = useRef({ p, picked, isPicked, pick });
  live.current = { p, picked, isPicked, pick };
  const gutter = useMemo<Gutter>(() => {
    const path = file.path;
    // Enter and `c` are read off the line group, so a click in the gutter hands focus back to it.
    const refocus = (e: MouseEvent) => e.currentTarget.closest<HTMLElement>(".rv-lines")?.focus({ preventScroll: true });
    const rowOf = (el: EventTarget | null) => (el instanceof Element ? el.closest<HTMLElement>("[data-hunk]") : null);
    // Shift extends on the pick's side, whichever of a row's numbers was pressed (as the arrows do).
    const extendTo = (a: Anchor, e: MouseEvent): Anchor => {
      const { picked } = live.current;
      if (!e.shiftKey || !picked || picked.side === a.side) return a;
      const line = Number(rowOf(e.currentTarget)?.dataset[picked.side]);
      return line ? { side: picked.side, line } : a;
    };
    // + comments on the pick when its line is in it, else on its own line.
    const plus = (a: Anchor) => {
      const { p, picked, isPicked } = live.current;
      if (picked && isPicked(a)) return p.onCompose(picked);
      const one = { path, side: a.side, anchor: a.line, head: a.line };
      p.onPick(one);
      p.onCompose(one);
    };
    // A press in the gutter starts a drag: the pick follows the pointer down the
    // lines of its hunk on the side it started on, at most once a frame, and a
    // drag that began on + opens the composer on the range when the button comes up.
    let frame = 0;
    const emit = () => {
      frame = 0;
      const d = drag.current;
      if (d?.moved) live.current.p.onPick({ path, side: d.side, anchor: d.anchor, head: d.head });
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
      const anchor = e.shiftKey && picked?.side === a.side ? picked.anchor : a.line;
      const button = e.currentTarget;
      drag.current = { side: a.side, anchor: keep ? a.line : anchor, head: a.line, from: a.line, hunk: rowOf(button)?.dataset.hunk, moved: false };
      if (!keep) p.onPick({ path, side: a.side, anchor, head: a.line });
      window.addEventListener(
        "mouseup",
        (up) => {
          cancelAnimationFrame(frame);
          emit();
          const d = drag.current;
          drag.current = null;
          if (!d || !isPlus) return;
          if (d.head !== d.from) live.current.p.onCompose({ path, side: d.side, anchor: d.anchor, head: d.head });
          // Let go on the row it started on: the same as a click on its + (a release on the + itself is that click).
          else if (!(up.target instanceof Node && button.contains(up.target))) plus(at);
        },
        { once: true },
      );
    };
    const onOver = (e: MouseEvent) => {
      const d = drag.current;
      const row = rowOf(e.target);
      // A row carries its line on each side it has one; a line on the other side only, or in another hunk, is passed over.
      const line = Number(row?.dataset[d?.side ?? "new"]);
      if (!d || !line || line === d.head || row!.dataset.hunk !== d.hunk) return;
      d.head = line;
      d.moved = true;
      if (!frame) frame = requestAnimationFrame(emit);
    };
    return { onClick: (a) => (e) => (live.current.pick(extendTo(a, e), e.shiftKey), refocus(e)), startDrag, onPlus: (a) => () => plus(a), onOver };
  }, [file.path]);
  const drag = useRef<{ side: Side; anchor: number; head: number; from: number; hunk: string | undefined; moved: boolean } | null>(null);

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
        <button type="button" className="rv-fold" aria-expanded={!collapsed} aria-label={collapsed ? `Expand ${file.path}` : `Collapse ${file.path}`} onClick={() => p.onCollapse(file.path, !collapsed)}>
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
            <div className={`rv-lines is-${p.prefs.layout}`} tabIndex={0} role="group" aria-label={`Lines of ${file.path}: arrows pick a line, Shift extends, Enter comments, n and p change file`} onKeyDown={onKey} onMouseOver={gutter.onOver}>
              {rows.map((r, i) => (
                <RowView
                  key={i}
                  row={r}
                  hunk={hunkOf[i]}
                  // Only the rows the pick touches see it, so the rest skip the render.
                  pick={picked && anchors[i].some(isPicked) ? picked : null}
                  readOnly={!!p.readOnly}
                  g={gutter}
                  slots={p.after ? anchors[i].map((a) => p.after!(file.path, a)) : NONE}
                />
              ))}
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
  onPlus: (a: Anchor) => () => void;
  onOver: (e: MouseEvent) => void;
}

const NONE: ReactNode[] = [];
const inPick = (pick: Pick | null, a: Anchor) => {
  if (!pick || pick.side !== a.side) return false;
  const [lo, hi] = pickRange(pick);
  return a.line >= lo && a.line <= hi;
};

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
function PlusButton({ a, pick, g }: { a: Anchor; pick: Pick | null; g: Gutter }) {
  const head = !!pick && pick.side === a.side && pick.head === a.line;
  const [start, end] = pick && inPick(pick, a) ? pickRange(pick) : [a.line, a.line];
  const name = rangeName({ side: a.side, start, end });
  const what = `Comment on ${name[0].toLowerCase()}${name.slice(1)}`;
  return (
    <button type="button" tabIndex={head ? 0 : -1} className={`rv-plus${head ? " is-head" : ""}`} aria-label={what} title={what} onMouseDown={g.startDrag(a, true)} onClick={g.onPlus(a)}>
      <Plus size={12} aria-hidden />
    </button>
  );
}

interface RowProps {
  row: Row;
  hunk: number;
  /** The pick, when it covers a line of this row; else null. */
  pick: Pick | null;
  readOnly: boolean;
  g: Gutter;
  /** What sits under each of the row's anchors (`anchorsOf`). */
  slots: ReactNode[];
}

const RowView = memo(function RowView({ row, hunk, pick, readOnly, g, slots }: RowProps) {
  if (row.t === "hunk") return <div className="rv-hunk rv-mono">{row.text}</div>;
  const kind = (c: Cell | null) => (c ? (c.kind === "+" ? " is-add" : c.kind === "-" ? " is-del" : "") : " is-none");
  const anchors = anchorsOf(row);
  const tail = slots.length > 0 && anchors.map((a, i) => <AfterSlot key={`${a.side}${a.line}`} node={slots[i]} />);
  if (row.t === "u") {
    const c = row.cell;
    // A context line is on both sides: each number picks its own, and + follows a pick on either.
    const plusAt = anchors.find((a) => inPick(pick, a)) ?? row.at;
    return (
      <>
        <div className={`rv-row${kind(c)}${inPick(pick, plusAt) ? " is-picked" : ""}`} data-hunk={hunk} data-old={c.old ?? undefined} data-new={c.new ?? undefined}>
          <Num shown={c.old} a={c.old === null ? row.at : { side: "old", line: c.old }} g={g} />
          <Num shown={c.new} a={c.new === null ? row.at : { side: "new", line: c.new }} g={g} />
          {!readOnly && <PlusButton a={plusAt} pick={pick} g={g} />}
          <span className="rv-mark" aria-hidden="true">{MARK[c.kind]}</span>
          <Code cell={c} />
        </div>
        {tail}
      </>
    );
  }
  const half = (c: Cell | null, side: Side) => {
    const a: Anchor | null = c ? { side, line: side === "old" ? c.old! : c.new! } : null;
    return (
      <div className={`rv-half${kind(c)}${a && inPick(pick, a) ? " is-picked" : ""}`}>
        {a ? <Num shown={a.line} a={a} g={g} /> : <span className="rv-num" />}
        {a && !readOnly && <PlusButton a={a} pick={pick} g={g} />}
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
}, (a, b) => a.row === b.row && a.hunk === b.hunk && a.pick === b.pick && a.readOnly === b.readOnly && a.g === b.g && a.slots.length === b.slots.length && a.slots.every((n, i) => n === b.slots[i]));

const AfterSlot = ({ node }: { node: ReactNode }) => (node ? <div className="rv-after">{node}</div> : null);
