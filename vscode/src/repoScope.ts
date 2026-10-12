import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import * as vscode from "vscode";
import { repoName } from "../../frontend/src/format";
import { ApiError, type Api, type Repo } from "./core/api";
import { mutating } from "./core/guard";
import { refetchRepos, REFETCH_MS, WorkspaceScope, type ConnectedRepo, type Git, type ScopeDeps } from "./core/repoScope";
import type { Store } from "./core/store";

const git: Git = (args, cwd) =>
  new Promise((ok) => {
    execFile("git", args, { cwd, timeout: 10_000 }, (err, stdout) => ok(err ? undefined : String(stdout).trim()));
  });

/** The real git and realpath, for anything else that asks a folder's repo. */
export const scopeDeps: ScopeDeps = { git, realpath: (p) => fs.realpath(p), platform: process.platform };

const sameList = (a: ConnectedRepo[], b: ConnectedRepo[]) =>
  a.length === b.length && a.every((r, i) => r.path === b[i].path && (r.managed ?? true) === (b[i].managed ?? true));

/** The window's scope, recomputed when its folders change and when the daemon
 *  it talks to has another `run_dir` or another set of connected repos. */
export function registerRepoScope(context: vscode.ExtensionContext, initialRunDir: string, api: Api, store: Store) {
  const scope = new WorkspaceScope(process.platform);
  let runDir = initialRunDir;
  let connected: ConnectedRepo[] = []; // the last list read
  let known: ConnectedRepo[] | undefined; // undefined while the last read failed
  let good: Repo[] | undefined; // the last list read that succeeded; undefined before the first
  let lastRead = -Infinity;
  let retry: ReturnType<typeof setTimeout> | undefined;
  const folders = () => (vscode.workspace.workspaceFolders ?? []).map((f) => f.uri.fsPath);
  const recompute = () => scope.update(folders(), runDir, scopeDeps, connected);
  const setAllRepos = () => void vscode.commands.executeCommand("setContext", "kraft.allRepos", scope.showAll);

  const read = async () => {
    lastRead = Date.now();
    if (retry) clearTimeout(retry);
    let repos: Repo[];
    try {
      repos = (await api.repos()).repos;
    } catch {
      known = undefined;
      retry = setTimeout(maybeRead, REFETCH_MS + 1_000);
      return;
    }
    known = repos;
    // The first success changes what `unconnected` answers even when the list is the empty one assumed before.
    const first = good === undefined;
    good = repos;
    if (!first && sameList(known, connected)) return;
    connected = known;
    await recompute();
  };
  // A repo connected after this window read the list would hide its items.
  const maybeRead = () => {
    if (store.connected && refetchRepos(store.items(), known, lastRead, Date.now(), process.platform)) void read();
  };

  context.subscriptions.push(
    vscode.workspace.onDidChangeWorkspaceFolders(() => void recompute()),
    vscode.commands.registerCommand("kraft.toggleAllRepos", () => {
      scope.toggleAll();
      setAllRepos();
    }),
    { dispose: store.onChange(maybeRead) },
    { dispose: () => retry && clearTimeout(retry) },
  );
  setAllRepos();
  void recompute();

  return {
    scope,
    /** On every connect: the daemon's `run_dir`, and a fresh read of its repos. */
    async setDaemon(nextRunDir: string): Promise<void> {
      if (nextRunDir !== runDir) {
        runDir = nextRunDir;
        void recompute();
      }
      await read();
    },
    /** Read `GET /repos` again now, and recompute the scope from it. */
    reread: () => read(),
    /** The folders' repo roots that are not connected (`WorkspaceScope.unconnected`). */
    unconnected: () => scope.unconnected(good),
  };
}

/** `kraft.connectRepo`: connect the first unconnected root, then read the list again. */
export function registerConnectRepo(
  context: vscode.ExtensionContext, api: Api, repoScope: { unconnected(): string[]; reread(): Promise<void> }, refusal: () => string | null,
) {
  const warn = (msg: string) => void vscode.window.showWarningMessage(msg);
  const run = async () => {
    const path = repoScope.unconnected()[0];
    if (path === undefined) return void vscode.window.showInformationMessage("Every repo in this window is already connected to Kraft.");
    try {
      await api.connectRepo(path);
      void vscode.window.showInformationMessage(`Connected ${repoName(path)}.`);
    } catch (e) {
      // Already connected (another window, the CLI): the outcome asked for.
      if (e instanceof ApiError && e.status === 409) void vscode.window.showInformationMessage(e.detail);
      else return void vscode.window.showErrorMessage(e instanceof ApiError ? e.detail : String(e));
    }
    await repoScope.reread();
  };
  context.subscriptions.push(vscode.commands.registerCommand("kraft.connectRepo", mutating("kraft.connectRepo", refusal, warn, run)));
}
