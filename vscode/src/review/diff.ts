import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { promisify } from "node:util";
import * as vscode from "vscode";
import { reportError } from "../actions";
import type { Api } from "../core/api";
import { leftUri, parseReviewUri, reviewFiles, rightUri } from "../core/review";
import type { Store } from "../core/store";

const run = promisify(execFile);

export function registerDiff(context: vscode.ExtensionContext, store: Store, api: Api) {
  const state: { lastOpened?: { id: string; files: string[] }; worktrees: Map<string, string> } = { worktrees: new Map() };

  const gitShow: vscode.TextDocumentContentProvider = {
    async provideTextDocumentContent(uri) {
      const { id, file } = parseReviewUri(uri.path);
      const cwd = state.worktrees.get(id);
      if (!cwd) return "";
      try {
        const { stdout } = await run("git", ["show", `${decodeURIComponent(uri.query)}:${file}`], { cwd, maxBuffer: 32 * 1024 * 1024 });
        return stdout;
      } catch (e) {
        // Only "not in the base" means an added file; anything else must surface, not read as an empty side.
        if (/exists on disk, but not in|does not exist in/.test(String((e as { stderr?: string }).stderr))) return "";
        throw e;
      }
    },
  };
  const worktreeFile: vscode.TextDocumentContentProvider = {
    async provideTextDocumentContent(uri) {
      const { id, file } = parseReviewUri(uri.path);
      const root = state.worktrees.get(id);
      if (!root) return "";
      try {
        return await readFile(join(root, file), "utf8");
      } catch (e) {
        if ((e as NodeJS.ErrnoException).code === "ENOENT") return ""; // deleted in the branch
        throw e;
      }
    },
  };

  const open = async (arg?: string | { item?: { id: string } }) => {
    const id = typeof arg === "string" ? arg : arg?.item?.id;
    if (!id) return;
    try {
      const diff = await api.getDiff(id);
      if (!diff.base_ref) return void vscode.window.showInformationMessage(`${id} has no base to compare against yet.`);
      state.worktrees.set(id, diff.worktree_path!);
      const files = reviewFiles(diff);
      if (files.length === 0) return void vscode.window.showInformationMessage(`${id} has no changes.`);
      state.lastOpened = { id, files };
      const resources = files.map((f) => [
        vscode.Uri.parse(rightUri(id, f)),
        vscode.Uri.parse(leftUri(id, f, diff.base_ref!)),
        vscode.Uri.parse(rightUri(id, f)),
      ]);
      const note = diff.truncated || diff.landed?.truncated ? " (the daemon truncated its diff; every file is still listed)" : "";
      await vscode.commands.executeCommand("vscode.changes", `Kraft ${id}: ${store.item(id)?.title ?? ""}${note}`, resources);
    } catch (e) {
      await reportError(e, store, id);
    }
  };

  context.subscriptions.push(
    vscode.workspace.registerTextDocumentContentProvider("kraft-git", gitShow),
    vscode.workspace.registerTextDocumentContentProvider("kraft-wt", worktreeFile),
    vscode.commands.registerCommand("kraft.reviewChanges", (arg?: string | { item?: { id: string } }) => {
      // From an artifact tab: the id is the first path segment.
      const uri = vscode.window.activeTextEditor?.document.uri;
      return open(arg ?? (uri?.scheme === "kraft-artifact" ? uri.path.split("/")[1] : undefined));
    }),
  );
  return state;
}
