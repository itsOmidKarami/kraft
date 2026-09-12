import { useMemo, useState, type CSSProperties } from "react";
import { CaretDown, CaretRight } from "@phosphor-icons/react";
import { Chip } from "../../../components/ui";
import type { WorkItemDiff } from "../../../types";
import { fileKey, type DiffSection } from "../selection";
import { buildFileTree, type TreeNode } from "./fileTree";

/**
 * Inspector · Changes (UI v2 · 05, 13 · UI v3 · 42, G4-01…04): a collapsible
 * folder tree, two sections — this node's own diff, and (optionally) what
 * already landed on the branch. Selecting a row scrolls `RightPane/Diff.tsx`
 * to that file; scrolling the pane moves the highlight back
 * (`RightPane/Diff.tsx`'s own `IntersectionObserver`). The diff is fetched
 * once, in `index.tsx`, and passed down here and to the pane.
 */

function matches(path: string, filter: string): boolean {
  if (!filter.trim()) return true;
  const pattern = filter.trim().replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*");
  try {
    return new RegExp(pattern, "i").test(path);
  } catch {
    return path.toLowerCase().includes(filter.toLowerCase());
  }
}

function filterFiles(nodes: TreeNode[], filter: string): TreeNode[] {
  if (!filter.trim()) return nodes;
  const out: TreeNode[] = [];
  for (const n of nodes) {
    if (n.kind === "file") {
      if (matches(n.path, filter)) out.push(n);
      continue;
    }
    const children = filterFiles(n.children, filter);
    if (children.length) out.push({ ...n, children, fileCount: children.reduce((c, x) => c + (x.kind === "file" ? 1 : x.fileCount), 0) });
  }
  return out;
}

function flatten(nodes: TreeNode[]): TreeNode[] {
  const out: TreeNode[] = [];
  for (const n of nodes) {
    if (n.kind === "file") out.push(n);
    else out.push(...flatten(n.children));
  }
  return out.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
}

function TreeRows({
  nodes,
  section,
  depth,
  collapsed,
  onToggleFolder,
  selected,
  onSelect,
}: {
  nodes: TreeNode[];
  section: DiffSection;
  depth: number;
  collapsed: Set<string>;
  onToggleFolder: (path: string) => void;
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  return (
    <>
      {nodes.map((n) =>
        n.kind === "folder" ? (
          <div key={n.path}>
            <button
              className="tree-row"
              data-kind="folder"
              style={{ "--depth": depth } as CSSProperties}
              onClick={() => onToggleFolder(n.path)}
            >
              {collapsed.has(n.path) ? <CaretRight size={12} /> : <CaretDown size={12} />}
              <span className="tree-name">{n.name}</span>
              <span className="row-sub">{n.fileCount} files</span>
            </button>
            {!collapsed.has(n.path) && (
              <TreeRows
                nodes={n.children}
                section={section}
                depth={depth + 1}
                collapsed={collapsed}
                onToggleFolder={onToggleFolder}
                selected={selected}
                onSelect={onSelect}
              />
            )}
          </div>
        ) : (
          <button
            key={n.path}
            className="tree-row"
            data-kind="file"
            data-selected={fileKey(section, n.path) === selected}
            style={{ "--depth": depth } as CSSProperties}
            onClick={() => onSelect(fileKey(section, n.path))}
          >
            <span />
            <span className="tree-name mono">{n.name}</span>
            <span>
              <span className="diff-add">+{n.insertions ?? 0}</span> <span className="diff-del">−{n.deletions ?? 0}</span>
            </span>
          </button>
        ),
      )}
    </>
  );
}

function TreeSection({
  label,
  section,
  summary,
  files,
  mode,
  filter,
  collapsed,
  onToggleFolder,
  selected,
  onSelect,
}: {
  label: string;
  section: DiffSection;
  summary: string;
  files: WorkItemDiff["files"];
  mode: "tree" | "flat";
  filter: string;
  collapsed: Set<string>;
  onToggleFolder: (path: string) => void;
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  const tree = filterFiles(buildFileTree(files), filter);
  const nodes = mode === "flat" ? flatten(tree) : tree;
  return (
    <div className="tree-section">
      <p className="section-label">
        {label} · {summary}
      </p>
      <TreeRows nodes={nodes} section={section} depth={0} collapsed={collapsed} onToggleFolder={onToggleFolder} selected={selected} onSelect={onSelect} />
    </div>
  );
}

export function Changes({
  diff,
  diffError,
  selected,
  onSelect,
}: {
  diff: WorkItemDiff | null;
  diffError: string | null;
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  const [filter, setFilter] = useState("");
  const [mode, setMode] = useState<"tree" | "flat">("tree");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggleFolder = (path: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      next.has(path) ? next.delete(path) : next.add(path);
      return next;
    });

  const totals = useMemo(
    () => (diff?.files ?? []).reduce((a, f) => ({ ins: a.ins + f.insertions, del: a.del + f.deletions }), { ins: 0, del: 0 }),
    [diff],
  );

  return (
    <div className="inspector-list" data-testid="inspector-changes">
      {diffError && <p className="form-error">{diffError}</p>}
      <div className="control-row">
        <input
          className="input"
          placeholder="filter files (e.g. *.tsx)"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <Chip label="tree" selected={mode === "tree"} onClick={() => setMode("tree")} />
        <Chip label="flat" selected={mode === "flat"} onClick={() => setMode("flat")} />
      </div>

      {diff && (
        <TreeSection
          label="This node"
          section="in-flight"
          summary={diff.files.length ? `${diff.files.length} files · +${totals.ins} −${totals.del}` : "0 files · nothing to diff"}
          files={diff.files}
          mode={mode}
          filter={filter}
          collapsed={collapsed}
          onToggleFolder={toggleFolder}
          selected={selected}
          onSelect={onSelect}
        />
      )}

      {diff?.landed && (
        <TreeSection
          label={`On this branch · ${diff.landed.commits.length} commit${diff.landed.commits.length === 1 ? "" : "s"}`}
          section="landed"
          summary={`${diff.landed.files.length} files · +${diff.landed.files.reduce((n, f) => n + f.insertions, 0)} −${diff.landed.files.reduce((n, f) => n + f.deletions, 0)}`}
          files={diff.landed.files}
          mode={mode}
          filter={filter}
          collapsed={collapsed}
          onToggleFolder={toggleFolder}
          selected={selected}
          onSelect={onSelect}
        />
      )}

      {diff?.untracked.filter((path) => matches(path, filter)).map((path) => (
        <button key={path} className="doc-row change-row" data-selected={path === selected} onClick={() => onSelect(path)}>
          <span className="doc-text">
            <span className="mono doc-title">{path}</span>
          </span>
          <span className="tag tag-outline tag-tight">new</span>
        </button>
      ))}
      <p className="inspector-foot">unified diff · Copy and Open in editor are in the pane</p>
    </div>
  );
}
