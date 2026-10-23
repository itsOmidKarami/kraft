import { useState, type CSSProperties, type ReactNode } from "react";
import { NoMatch } from "../ui/NoMatch";
import { plural } from "../../format";
import type { CompareFile, ReviewThread } from "../../types";
import { ChevronDown, ChevronRight, File, FileCode, FileCog, FileTerminal, FileText } from "../icons";
import { inTreeOrder, threadSummary, tree, unresolved, type Folder } from "./model";
import { familyOf, languageOf, type Family } from "./languages";

/** A row's indent, one step per folder it sits in. */
const depth = (d: number) => ({ "--depth": d }) as CSSProperties;

/** The deepest row's depth: a folder's row sits at `d`, its files one deeper. */
const deepest = (f: Folder, d: number): number => Math.max(f.files.length ? d : 0, ...f.folders.map((s) => Math.max(d, deepest(s, d + 1))));

/** 12px a level, narrowed on a deep tree so the deepest row's indent stays within 72px of the 260px column and its name keeps room. */
const indentStep = (root: Folder) => ({ "--step": `${Math.min(12, Math.floor(72 / Math.max(1, deepest(root, 0))))}px` }) as CSSProperties;

/** A file's icon, by the language the diff reads it as. */
const ICON: Record<Family, typeof File> = { clike: FileCode, python: FileCode, shell: FileTerminal, yaml: FileCog, markdown: FileText, plain: File };

/** The file list beside the diff (prototype 412–417): counts and threads per
 *  file, each with its kind's icon and dimmed once viewed (the mark itself is
 *  set on the file's header), nested folders that fold, a filter (GAP §2 #3). */
export function FileTree(p: {
  files: CompareFile[];
  untracked: string[];
  /** Listed but past the diff's size cut. */
  notShown: Set<string>;
  threads: ReviewThread[];
  selected: string | null;
  isViewed: (path: string) => boolean;
  onSelect: (path: string) => void;
  error: string | null;
}) {
  const [filter, setFilter] = useState("");
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const add = p.files.reduce((n, f) => n + f.insertions, 0);
  const del = p.files.reduce((n, f) => n + f.deletions, 0);
  const viewedN = p.files.filter((f) => p.isViewed(f.path)).length;
  const open = (path: string) => p.threads.filter((t) => t.file_path === path && unresolved(t)).length;
  const fold = (dir: string) => setClosed((c) => {
    const n = new Set(c);
    if (n.has(dir)) n.delete(dir);
    else n.add(dir);
    return n;
  });
  // One level of the tree: its folders, each folding what is under it, then its files.
  const level = (folder: Folder, d: number): ReactNode => (
    <>
      {folder.folders.map((sub) => (
        <div key={sub.dir} className="rv-folder">
          <button type="button" className="rv-folder-row" style={depth(d)} aria-expanded={!closed.has(sub.dir)} onClick={() => fold(sub.dir)}>
            {closed.has(sub.dir) ? <ChevronRight className="rv-folder-icon" size={14} aria-hidden /> : <ChevronDown className="rv-folder-icon" size={14} aria-hidden />}
            <span className="rv-mono" title={sub.dir} data-allow-ellipsis="">{sub.name}</span>
          </button>
          {!closed.has(sub.dir) && level(sub, d + 1)}
        </div>
      ))}
      {folder.files.map((f) => {
        const v = p.isViewed(f.path);
        const n = open(f.path);
        const lang = familyOf(languageOf(f.path));
        const Icon = ICON[lang];
        return (
          <div key={f.path} className={`rv-file-row${p.selected === f.path ? " is-on" : ""}${v ? " is-viewed" : ""}`} style={depth(d)}>
            <Icon className="rv-file-icon" data-lang={lang} size={14} aria-hidden />
            <button type="button" className="rv-file-name" data-allow-ellipsis="" aria-current={p.selected === f.path ? "true" : undefined} title={f.path} onClick={() => p.onSelect(f.path)}>
              <span className="rv-mono">{f.path.slice(folder.dir.length)}</span>
              {p.notShown.has(f.path) && <span className="rv-muted"> not shown</span>}
            </button>
            {v && <span className="review-visually-hidden">viewed</span>}
            {n > 0 && <span className="rv-tree-threads" title={`${n} open thread${n === 1 ? "" : "s"}`}>{n}</span>}
            <span className="rv-add">+{f.insertions}</span>
            <span className="rv-del">−{f.deletions}</span>
          </div>
        );
      })}
    </>
  );
  const root = tree(p.files, filter);
  const shownN = inTreeOrder(root).length;
  const untracked = p.untracked.filter((u) => u.toLowerCase().includes(filter.trim().toLowerCase()));
  return (
    <nav className="rv-tree" aria-label="Changed files">
      <div className="rv-tree-head">
        <span className="rv-tree-count">{filter.trim() ? `${shownN} of ${plural(p.files.length, "file")}` : plural(p.files.length, "file")}</span>
        <span className="rv-add">+{add}</span>
        <span className="rv-del">−{del}</span>
      </div>
      <input className="rv-tree-filter" type="search" placeholder="Filter files" aria-label="Filter files" value={filter} onChange={(e) => setFilter(e.target.value)} />
      <div className="rv-tree-body" style={indentStep(root)}>
        {level(root, 0)}
        {filter.trim() && !shownN && !untracked.length && <NoMatch className="rv-tree-empty" query={filter} onClear={() => setFilter("")} />}
        {untracked.length > 0 && (
          <div className="rv-folder">
            <span className="rv-folder-row is-static">Untracked (not in the diff)</span>
            {untracked.map((u) => (
              <div key={u} className="rv-file-row" style={depth(1)}><span className="rv-mono rv-muted" title={u}>{u}</span></div>
            ))}
          </div>
        )}
      </div>
      <div className="rv-tree-foot">
        <span>{viewedN} of {p.files.length} viewed</span>
        <span>{threadSummary(p.threads)}</span>
        {p.error && <span className="rv-error" role="alert">{p.error}</span>}
      </div>
    </nav>
  );
}
