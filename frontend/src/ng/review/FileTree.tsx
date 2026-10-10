import { useState } from "react";
import { plural } from "../../format";
import type { CompareFile, ReviewThread } from "../../types";
import { File, FileCode, FileCog, FileTerminal, FileText } from "../icons";
import { folders, threadSummary, unresolved } from "./model";
import { languageOf, type Lang } from "./tokenize";

/** A file's icon, by the language the diff reads it as. */
const ICON: Record<Lang, typeof File> = { clike: FileCode, python: FileCode, shell: FileTerminal, yaml: FileCog, markdown: FileText, plain: File };

/** The file list beside the diff (prototype 412–417): counts and threads per
 *  file, each with its kind's icon and dimmed once viewed (the mark itself is
 *  set on the file's header), folders that fold, a filter (GAP §2 #3). */
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
  const untracked = p.untracked.filter((u) => u.toLowerCase().includes(filter.trim().toLowerCase()));
  return (
    <nav className="rv-tree" aria-label="Changed files">
      <div className="rv-tree-head">
        <span className="rv-tree-count">{plural(p.files.length, "file")}</span>
        <span className="rv-add">+{add}</span>
        <span className="rv-del">−{del}</span>
      </div>
      <input className="rv-tree-filter" type="search" placeholder="Filter files" aria-label="Filter files" value={filter} onChange={(e) => setFilter(e.target.value)} />
      <div className="rv-tree-body">
        {folders(p.files, filter).map(({ dir, files }) => (
          <div key={dir || "."} className="rv-folder">
            {dir && (
              <button type="button" className="rv-folder-row" aria-expanded={!closed.has(dir)} onClick={() => fold(dir)}>
                <span aria-hidden="true">{closed.has(dir) ? "▸" : "▾"}</span>
                <span className="rv-mono" title={dir} data-allow-ellipsis="">{dir}</span>
              </button>
            )}
            {!closed.has(dir) &&
              files.map((f) => {
                const v = p.isViewed(f.path);
                const n = open(f.path);
                const lang = languageOf(f.path);
                const Icon = ICON[lang];
                return (
                  <div key={f.path} className={`rv-file-row${dir ? " is-nested" : ""}${p.selected === f.path ? " is-on" : ""}${v ? " is-viewed" : ""}`}>
                    <Icon className="rv-file-icon" data-lang={lang} size={13} aria-hidden />
                    <button type="button" className="rv-file-name" data-allow-ellipsis="" aria-current={p.selected === f.path ? "true" : undefined} title={f.path} onClick={() => p.onSelect(f.path)}>
                      <span className="rv-mono">{f.path.slice(dir.length)}</span>
                      {p.notShown.has(f.path) && <span className="rv-muted"> not shown</span>}
                    </button>
                    {v && <span className="review-visually-hidden">viewed</span>}
                    {n > 0 && <span className="rv-tree-threads" title={`${n} open thread${n === 1 ? "" : "s"}`}>{n}</span>}
                    <span className="rv-add">+{f.insertions}</span>
                    <span className="rv-del">−{f.deletions}</span>
                  </div>
                );
              })}
          </div>
        ))}
        {untracked.length > 0 && (
          <div className="rv-folder">
            <span className="rv-folder-row is-static">Untracked (not in the diff)</span>
            {untracked.map((u) => (
              <div key={u} className="rv-file-row is-nested"><span className="rv-mono rv-muted" title={u}>{u}</span></div>
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
