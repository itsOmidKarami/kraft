import { useEffect, useMemo, useRef, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import type { CompareFile } from "../../types";
import { EllipsisVertical, MessageSquare } from "../icons";
import { IconButton } from "../ui/IconButton";
import { Menu } from "../ui/Menu";
import { showToast } from "../ui/Toast";
import type { PatchFile } from "./patch";
import type { DiffPrefs } from "./prefs";
import { anchorsOf, buildRows, spans, type Anchor, type Cell, type Row, type Side } from "./rows";
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

  const pick = (a: Anchor, extend: boolean) =>
    !p.readOnly &&
    p.onPick(extend && picked && picked.side === a.side ? { ...picked, head: a.line } : { path: file.path, side: a.side, anchor: a.line, head: a.line });
  const onClick = (a: Anchor) => (e: MouseEvent) => {
    pick(a, e.shiftKey);
    // Enter and `c` are read off the line group, so a click on a number hands focus back to it.
    e.currentTarget.closest<HTMLElement>(".rv-lines")?.focus({ preventScroll: true });
  };

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
            <div className={`rv-lines is-${p.prefs.layout}`} tabIndex={0} role="group" aria-label={`Lines of ${file.path}: arrows pick a line, Shift extends, Enter comments, n and p change file`} onKeyDown={onKey}>
              {rows.map((r, i) => (
                <RowView key={i} row={r} isPicked={isPicked} onClick={onClick} after={p.after && ((a) => p.after!(file.path, a))} />
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

function RowView({ row, isPicked, onClick, after }: { row: Row; isPicked: (a: Anchor) => boolean; onClick: (a: Anchor) => (e: MouseEvent) => void; after?: (a: Anchor) => ReactNode }) {
  if (row.t === "hunk") return <div className="rv-hunk rv-mono">{row.text}</div>;
  const kind = (c: Cell | null) => (c ? (c.kind === "+" ? " is-add" : c.kind === "-" ? " is-del" : "") : " is-none");
  const tail = after && anchorsOf(row).map((a) => <AfterSlot key={`${a.side}${a.line}`} node={after(a)} />);
  if (row.t === "u") {
    const c = row.cell;
    return (
      <>
        <div className={`rv-row${kind(c)}${isPicked(row.at) ? " is-picked" : ""}`}>
          <button type="button" tabIndex={-1} className="rv-num" aria-label={`Pick ${row.at.side} line ${row.at.line}`} onClick={onClick(row.at)}>{c.old ?? ""}</button>
          <button type="button" tabIndex={-1} className="rv-num" aria-hidden="true" onClick={onClick(row.at)}>{c.new ?? ""}</button>
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
      <div className={`rv-half${kind(c)}${a && isPicked(a) ? " is-picked" : ""}`}>
        {a ? <button type="button" tabIndex={-1} className="rv-num" aria-label={`Pick ${side} line ${a.line}`} onClick={onClick(a)}>{a.line}</button> : <span className="rv-num" />}
        <span className="rv-mark" aria-hidden="true">{c ? MARK[c.kind] : ""}</span>
        {c ? <Code cell={c} /> : <code className="rv-code" />}
      </div>
    );
  };
  return (
    <>
      <div className="rv-row is-split">
        {half(row.left, "old")}
        {half(row.right, "new")}
      </div>
      {tail}
    </>
  );
}

const AfterSlot = ({ node }: { node: ReactNode }) => (node ? <div className="rv-after">{node}</div> : null);
