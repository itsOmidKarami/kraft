import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { promisify } from "node:util";
import * as vscode from "vscode";
import { reportError } from "../actions";
import type { Api } from "../core/api";
import { commandItemId, leftUri, parseRenames, parseReviewUri, reviewFiles, rightUri } from "../core/review";
import type { Store } from "../core/store";

const run = promisify(execFile);

/** A review as last opened: its files, the base it compares against, and its renames (new path → old path). */
export interface Opened { files: string[]; baseRef: string; renames: Map<string, string>; dirty: Set<string> }

export function registerDiff(context: vscode.ExtensionContext, store: Store, api: Api, runDir: () => string, previewId: (arg: unknown) => string | undefined) {
  const openedL = new Set<(id: string) => void>();
  const state: {
    lastOpened?: { id: string; files: string[] };
    worktrees: Map<string, string>;
    opened: Map<string, Opened>;
    onOpened(cb: (id: string) => void): () => void;
  } = {
    worktrees: new Map(),
    opened: new Map(),
    onOpened: (cb) => (openedL.add(cb), () => openedL.delete(cb)),
  };

  // The base against the working tree, as the server reads a rename for a thread (`review.renamed_from`).
  const renamesOf = async (cwd: string, base: string): Promise<Map<string, string>> => {
    try {
      const { stdout } = await run("git", ["diff", "-M", "--name-status", "-z", "--diff-filter=R", base], { cwd, maxBuffer: 32 * 1024 * 1024 });
      return parseRenames(stdout);
    } catch {
      return new Map(); // no renames read: each old side reads its new path, as before
    }
  };

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
      // Before the diff opens (a click on a finding in Problems), the worktree is at its usual place.
      const root = state.worktrees.get(id) ?? join(runDir(), "worktrees", id);
      try {
        return await readFile(join(root, file), "utf8");
      } catch (e) {
        if ((e as NodeJS.ErrnoException).code === "ENOENT") return ""; // deleted in the branch
        throw e;
      }
    },
  };

  const open = async (id?: string) => {
    if (!id) return;
    try {
      const diff = await api.getDiff(id);
      if (!diff.base_ref) return void vscode.window.showInformationMessage(`${id} has no base to compare against yet.`);
      state.worktrees.set(id, diff.worktree_path!);
      const files = reviewFiles(diff);
      if (files.length === 0) return void vscode.window.showInformationMessage(`${id} has no changes.`);
      const renames = diff.worktree_path ? await renamesOf(diff.worktree_path, diff.base_ref) : new Map<string, string>();
      state.lastOpened = { id, files };
      // Uncommitted and untracked: their branch side is the working tree, ahead of HEAD.
      const dirty = new Set([...(diff.files ?? []).map((f) => f.path), ...(diff.untracked ?? [])]);
      state.opened.set(id, { files, baseRef: diff.base_ref, renames, dirty });
      // A renamed file's base side is its old path.
      const resources = files.map((f) => [
        vscode.Uri.parse(rightUri(id, f)),
        vscode.Uri.parse(leftUri(id, renames.get(f) ?? f, diff.base_ref!)),
        vscode.Uri.parse(rightUri(id, f)),
      ]);
      const note = diff.truncated || diff.landed?.truncated ? " (the daemon truncated its diff; every file is still listed)" : "";
      await vscode.commands.executeCommand("vscode.changes", `Kraft ${id}: ${store.item(id)?.title ?? ""}${note}`, resources);
      openedL.forEach((cb) => cb(id));
    } catch (e) {
      await reportError(e, store, id);
    }
  };

  context.subscriptions.push(
    vscode.workspace.registerTextDocumentContentProvider("kraft-git", gitShow),
    vscode.workspace.registerTextDocumentContentProvider("kraft-wt", worktreeFile),
    vscode.commands.registerCommand("kraft.reviewChanges", (arg?: unknown) =>
      open(commandItemId(arg, vscode.window.activeTextEditor?.document.uri, ["kraft-artifact"]) ?? previewId(arg)),
    ),
  );
  return state;
}
