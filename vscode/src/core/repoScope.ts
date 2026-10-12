/** A workspace folder's repo, computed the way Kraft stores `repo`
 *  (`config.normalized_repo_root`, `client/context.py` `resolve_repo`), so a
 *  subfolder, symlink, linked worktree or submodule window finds its items.
 *  Pure: git and realpath are injected, paths are strings (win32 too). */

import type { WorkItem } from "./api";

/** Trimmed stdout, `undefined` when git fails or is absent. */
export type Git = (args: string[], cwd: string) => Promise<string | undefined>;
export interface ScopeDeps { git: Git; realpath: (p: string) => Promise<string>; platform: NodeJS.Platform }
export interface FolderScope { roots: string[]; worktreeItem?: string }
/** An entry of `GET /repos`; `managed: false` is an auto-connected child. */
export interface ConnectedRepo { path: string; managed?: boolean }

const SEPS = /[\\/]/;

function trimSeps(p: string, win: boolean): string {
  const t = p.replace(win ? /[\\/]+$/ : /\/+$/, "");
  return t || p.slice(0, 1);
}

function norm(p: string, platform: NodeJS.Platform): string {
  const win = platform === "win32";
  const t = trimSeps(p, win);
  return win ? t.replace(/\//g, "\\").toLowerCase() : t;
}

export function samePath(a: string, b: string, platform: NodeJS.Platform): boolean {
  return norm(a, platform) === norm(b, platform);
}

function parent(p: string): string | undefined {
  const t = trimSeps(p, true);
  const i = Math.max(t.lastIndexOf("/"), t.lastIndexOf("\\"));
  if (i < 0 || i === t.length - 1) return undefined;
  const up = t.slice(0, i);
  if (up === "") return t[0];
  return /^[A-Za-z]:$/.test(up) ? up + t[i] : up;
}

// git < 2.31 does not know --path-format: it echoes the flag as a line and
// prints the common dir relative, exit 0. Only one absolute line is trusted.
function absoluteLine(out: string | undefined): string | undefined {
  if (!out || out.includes("\n")) return undefined;
  return /^(\/|[A-Za-z]:[\\/])/.test(out) ? out : undefined;
}

export async function repoRoot(folder: string, deps: ScopeDeps): Promise<string> {
  const real = await deps.realpath(folder).catch(() => folder);
  const [top, common] = await Promise.all([
    deps.git(["rev-parse", "--show-toplevel"], real),
    deps.git(["rev-parse", "--path-format=absolute", "--git-common-dir"], real),
  ]);
  if (top === undefined) return real;
  const dir = absoluteLine(common);
  if (dir !== undefined && trimSeps(dir, true).split(SEPS).pop() === ".git") return parent(dir) ?? top;
  return top;
}

export function worktreeItemId(folder: string, runDir: string, platform: NodeJS.Platform): string | undefined {
  if (!runDir) return undefined;
  const win = platform === "win32";
  const sep = win ? "\\" : "/";
  const f = win ? folder.replace(/\//g, "\\") : folder;
  const prefix = `${trimSeps(win ? runDir.replace(/\//g, "\\") : runDir, win)}${sep}worktrees${sep}`;
  const head = f.slice(0, prefix.length);
  if (win ? head.toLowerCase() !== prefix.toLowerCase() : head !== prefix) return undefined;
  return f.slice(prefix.length).split(sep)[0] || undefined;
}

/** The nearest managed connected entry at or above `root`, walking past
 *  unmanaged ones as `resolve_repo` does: an item filed from a submodule is
 *  stored under the superproject. */
function managedAncestor(root: string, connected: ConnectedRepo[], platform: NodeJS.Platform): string | undefined {
  for (let at: string | undefined = root; at !== undefined; at = parent(at)) {
    const hit = connected.find((c) => c.managed !== false && samePath(c.path, at!, platform));
    if (hit) return at;
  }
  return undefined;
}

export async function folderScope(
  folder: string, runDir: string, deps: ScopeDeps, connected: ConnectedRepo[] = [], rootOf: typeof repoRoot = repoRoot,
): Promise<FolderScope> {
  const real = await deps.realpath(folder).catch(() => folder);
  const realRun = await deps.realpath(runDir).catch(() => runDir);
  const root = await rootOf(folder, deps);
  const roots: string[] = [];
  for (const p of [folder, real, root, managedAncestor(root, connected, deps.platform)]) {
    if (p !== undefined && !roots.some((r) => samePath(r, p, deps.platform))) roots.push(p);
  }
  const worktreeItem = worktreeItemId(real, realRun, deps.platform) ?? worktreeItemId(folder, runDir, deps.platform);
  return worktreeItem === undefined ? { roots } : { roots, worktreeItem };
}

/** The one scope the board, the badge and the toasts share. */
export class WorkspaceScope {
  private scopes: FolderScope[] = [];
  private _ready = false;
  private _showAll = false;
  private gen = 0;
  private listeners = new Set<() => void>();
  // git runs once per folder: a new run_dir or connected list redoes only the cheap steps.
  private rootCache = new Map<string, Promise<string>>();

  constructor(private platform: NodeJS.Platform) {}

  /** False until the first update() settles: nothing is in scope before. */
  get ready(): boolean { return this._ready; }
  get showAll(): boolean { return this._showAll; }

  toggleAll(): void {
    this._showAll = !this._showAll;
    this.fire();
  }

  /** git is slow and folders change under it: the last call started wins. */
  async update(folders: string[], runDir: string, deps: ScopeDeps, connected: ConnectedRepo[] = []): Promise<void> {
    const gen = ++this.gen;
    for (const f of this.rootCache.keys()) if (!folders.includes(f)) this.rootCache.delete(f);
    const rootOf = (f: string, d: ScopeDeps) => {
      let root = this.rootCache.get(f);
      if (!root) {
        root = repoRoot(f, d);
        this.rootCache.set(f, root);
        root.catch(() => this.rootCache.delete(f));
      }
      return root;
    };
    const scopes = await Promise.all(
      folders.map((f) => folderScope(f, runDir, deps, connected, rootOf).catch((): FolderScope => {
        const worktreeItem = worktreeItemId(f, runDir, this.platform);
        return worktreeItem === undefined ? { roots: [f] } : { roots: [f], worktreeItem };
      })),
    );
    if (gen !== this.gen) return;
    this.scopes = scopes;
    this._ready = true;
    this.fire();
  }

  includes(item: Pick<WorkItem, "id" | "repo">, repoOf: (id: string) => string | undefined): boolean {
    if (this._showAll) return true;
    if (!this._ready) return false;
    const same = (p: string | undefined) => p !== undefined && samePath(item.repo, p, this.platform);
    return this.scopes.some(
      (s) => s.roots.some(same) || (s.worktreeItem !== undefined && (s.worktreeItem === item.id || same(repoOf(s.worktreeItem)))),
    );
  }

  onChange(cb: () => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  private fire(): void {
    for (const cb of this.listeners) cb();
  }
}

/** At most one `GET /repos` per this long when the reason to read it again persists. */
export const REFETCH_MS = 30_000;

/** Whether to read `GET /repos` again: the last read failed (`known`
 *  undefined), or an item is in a repo the list does not have, which a repo
 *  connected since this window last read it would be. */
export function refetchRepos(
  items: Pick<WorkItem, "repo">[], known: ConnectedRepo[] | undefined, lastRead: number, now: number, platform: NodeJS.Platform,
): boolean {
  if (now - lastRead < REFETCH_MS) return false;
  if (!known) return true;
  return items.some((i) => !known.some((c) => samePath(c.path, i.repo, platform)));
}
