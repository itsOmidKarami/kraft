/** A workspace folder's repo, computed the way Kraft stores `repo`
 *  (`config.normalized_repo_root`, `client/context.py` `resolve_repo`), so a
 *  subfolder, symlink, linked worktree or submodule window finds its items.
 *  Pure: git and realpath are injected, paths are strings (win32 too). */

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

export async function folderScope(folder: string, runDir: string, deps: ScopeDeps, connected: ConnectedRepo[] = []): Promise<FolderScope> {
  const real = await deps.realpath(folder).catch(() => folder);
  const realRun = await deps.realpath(runDir).catch(() => runDir);
  const root = await repoRoot(folder, deps);
  const roots: string[] = [];
  for (const p of [folder, real, root, managedAncestor(root, connected, deps.platform)]) {
    if (p !== undefined && !roots.some((r) => samePath(r, p, deps.platform))) roots.push(p);
  }
  const worktreeItem = worktreeItemId(real, realRun, deps.platform) ?? worktreeItemId(folder, runDir, deps.platform);
  return worktreeItem === undefined ? { roots } : { roots, worktreeItem };
}
