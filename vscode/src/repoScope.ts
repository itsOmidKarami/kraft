import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import * as vscode from "vscode";
import { WorkspaceScope, type ConnectedRepo, type Git, type ScopeDeps } from "./core/repoScope";

const git: Git = (args, cwd) =>
  new Promise((ok) => {
    execFile("git", args, { cwd, timeout: 10_000 }, (err, stdout) => ok(err ? undefined : String(stdout).trim()));
  });

const deps: ScopeDeps = { git, realpath: (p) => fs.realpath(p), platform: process.platform };

const sameList = (a: ConnectedRepo[], b: ConnectedRepo[]) =>
  a.length === b.length && a.every((r, i) => r.path === b[i].path && (r.managed ?? true) === (b[i].managed ?? true));

/** The window's scope, recomputed when its folders change and when the daemon
 *  it talks to has another `run_dir` or another set of connected repos. */
export function registerRepoScope(context: vscode.ExtensionContext, initialRunDir: string) {
  const scope = new WorkspaceScope(process.platform);
  let runDir = initialRunDir;
  let connected: ConnectedRepo[] = [];
  const folders = () => (vscode.workspace.workspaceFolders ?? []).map((f) => f.uri.fsPath);
  const recompute = () => scope.update(folders(), runDir, deps, connected);
  const setAllRepos = () => void vscode.commands.executeCommand("setContext", "kraft.allRepos", scope.showAll);

  context.subscriptions.push(
    vscode.workspace.onDidChangeWorkspaceFolders(() => void recompute()),
    vscode.commands.registerCommand("kraft.toggleAllRepos", () => {
      scope.toggleAll();
      setAllRepos();
    }),
  );
  setAllRepos();
  void recompute();

  return {
    scope,
    async setDaemon(nextRunDir: string, nextConnected: ConnectedRepo[]): Promise<void> {
      if (nextRunDir === runDir && sameList(nextConnected, connected)) return;
      runDir = nextRunDir;
      connected = nextConnected;
      await recompute();
    },
  };
}
