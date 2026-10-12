import { promises as fs } from "node:fs";
import { basename, dirname } from "node:path";
import * as vscode from "vscode";
import { reportError } from "./actions";
import { ApiError, type Api, type Repo } from "./core/api";
import {
  abandonRefusal, attachChoices, attachState, chainsFor, cwdFor, createBody, draftNote, draftUrl, outcomeMessage, pickRepo, previewLine, repoDefaultChain,
  titleFrom, titleProblem, warningActions, warningsOf, type FileDraft,
} from "./core/fileWork";
import { mutating } from "./core/guard";
import { folderScope, type ScopeDeps } from "./core/repoScope";
import type { Store } from "./core/store";

type Kind = "spec" | "plan";
/** What the command was started from: an Explorer file to attach as `as`, or an editor selection. */
interface Seed { uri?: vscode.Uri; as?: Kind; selection?: string }

export interface FileWorkDeps {
  api: Api;
  store: Store;
  reveal: (id: string) => Promise<boolean>;
  /** Restricted Mode, a refused version, then the local-only rule (`fileRefusal`). */
  refusal: () => string | null;
  runDir: () => string;
  scope: ScopeDeps;
}

/** A step the person cancelled, or one that already said why it stopped. */
class Stop extends Error {}
const stop = (msg?: string): never => {
  if (msg) void vscode.window.showWarningMessage(msg);
  throw new Stop();
};
const asked = <T>(v: T | undefined): T => (v === undefined ? stop() : v);

async function folderOf(seed: Seed): Promise<vscode.WorkspaceFolder> {
  const uri = seed.uri ?? vscode.window.activeTextEditor?.document.uri;
  const own = uri && vscode.workspace.getWorkspaceFolder(uri);
  if (own) return own;
  const folders = vscode.workspace.workspaceFolders ?? [];
  if (folders.length === 1) return folders[0];
  if (folders.length === 0) return stop("Open a folder in the repo to file work in.");
  return asked(await vscode.window.showWorkspaceFolderPick({ placeHolder: "File work in which folder's repo?" }));
}

/** The path the daemon reads: a local file's realpath, or a remote window's own POSIX path. */
async function attachable(uri: vscode.Uri, folder: vscode.WorkspaceFolder): Promise<string> {
  const doc = vscode.workspace.textDocuments.find((d) => d.uri.toString() === uri.toString());
  const name = basename(uri.path);
  const state = attachState({ scheme: uri.scheme, isUntitled: doc?.isUntitled, isDirty: doc?.isDirty }, folder.uri.scheme);
  if (typeof state === "object") return stop(state.refusal);
  if (state === "save") {
    const ok = await vscode.window.showWarningMessage(`${name} has unsaved changes. Save it to attach it.`, { modal: true }, "Save");
    if (ok !== "Save" || !(await doc!.save())) return stop(`Save ${name} first: Kraft attaches the file as it is on disk.`);
  }
  return uri.scheme === "file" ? fs.realpath(uri.fsPath) : uri.path;
}

async function attachmentsOf(seed: Seed, folder: vscode.WorkspaceFolder): Promise<Partial<Record<Kind, vscode.Uri>>> {
  if (seed.as) return seed.uri ? { [seed.as]: seed.uri } : stop("Pick a Markdown file to file it as a spec or plan.");
  const active = vscode.window.activeTextEditor?.document.uri;
  const activeMd = active && active.path.endsWith(".md") ? active : undefined;
  const choice = asked(await vscode.window.showQuickPick(attachChoices(activeMd?.path), { title: "Attach documents?" }));
  if (!choice.pick) {
    return { ...(choice.spec ? { spec: activeMd } : {}), ...(choice.plan ? { plan: activeMd } : {}) };
  }
  const defaultUri = activeMd ? vscode.Uri.joinPath(activeMd, "..") : folder.uri;
  const out: Partial<Record<Kind, vscode.Uri>> = {};
  for (const kind of ["spec", "plan"] as const) {
    const picked = await vscode.window.showOpenDialog({
      defaultUri, canSelectMany: false, filters: { Markdown: ["md"] },
      title: `The ${kind} to attach (cancel for none)`, openLabel: `Attach as ${kind}`,
    });
    if (picked?.[0]) out[kind] = picked[0];
  }
  return out;
}

async function chainOf(api: Api, repo: Repo): Promise<{ chain?: string; label: string }> {
  const all = await api.chains();
  const fallback = repoDefaultChain(repo, all);
  const items = [
    { label: "Repo default", description: fallback, chain: undefined as string | undefined },
    ...chainsFor(all, repo).map((c) => ({ label: c.id, description: `${c.nodes.length} nodes · ${c.gates} gates`, chain: c.id as string | undefined })),
  ];
  const picked = asked(await vscode.window.showQuickPick(items, { title: "Which chain?" }));
  return { chain: picked.chain, label: picked.chain ?? `repo default (${fallback})` };
}

async function offerWarnings(deps: FileWorkDeps, id: string, warnings: string[], actions: ("Open item" | "Abandon")[]): Promise<void> {
  const { api, store } = deps;
  const picked = await vscode.window.showWarningMessage(warnings.join(" "), ...actions);
  if (picked === "Open item") {
    void vscode.env.openExternal(vscode.Uri.parse(`${api.base}/work-items/${encodeURIComponent(id)}`));
  } else if (picked === "Abandon") {
    // D24: decided now, not when the item was filed: it may have started since.
    const no = abandonRefusal(id, store.item(id));
    if (no) return void vscode.window.showInformationMessage(no);
    const ok = await vscode.window.showWarningMessage(`Abandon ${id}? Kraft deletes its worktree and branch.`, { modal: true }, "Abandon");
    if (ok !== "Abandon") return;
    // The server has the last word: a 409 shows its detail.
    try {
      await api.abandon(id);
      await store.refresh(id);
    } catch (e) {
      await reportError(e, store, id);
    }
  }
}

async function fileWork(deps: FileWorkDeps, seed: Seed): Promise<void> {
  const { api, store } = deps;
  const folder = await folderOf(seed);
  const { repos } = await api.repos();
  const { roots } = await folderScope(folder.uri.fsPath, deps.runDir(), deps.scope, repos);
  const found = pickRepo(roots, repos, process.platform);
  if ("refusal" in found) return stop(found.refusal);
  const { repo } = found;

  const title = asked(await vscode.window.showInputBox({
    title: `File work in ${repo.path}`, prompt: "Title", value: titleFrom(seed.selection ?? ""), validateInput: titleProblem,
  }));
  // A selection is the description as it is: an input box is one line and would join its lines.
  const description = seed.selection !== undefined ? seed.selection.trim() : asked(await vscode.window.showInputBox({
    title: "Description (optional)", prompt: "Context, constraints, what done looks like. Every node reads it.",
  }));

  const uris = await attachmentsOf(seed, folder);
  const paths: Partial<Record<Kind, string>> = {};
  for (const kind of ["spec", "plan"] as const) {
    const uri = uris[kind];
    if (uri) paths[kind] = await attachable(uri, folder);
  }
  // The worktree a document outside the repo is in widens where the server looks: its own toplevel, not
  // repoRoot's main checkout, which the server already has. A remote window's path is on the daemon's machine,
  // which resolves the toplevel of the folder itself (`_attachment_roots`).
  const tops = await Promise.all(Object.values(paths).map(async (path) => {
    const dir = dirname(path);
    const top = folder.uri.scheme === "file" ? await deps.scope.git(["rev-parse", "--show-toplevel"], dir) : undefined;
    return { path, top: top ?? dir };
  }));
  const where = cwdFor(tops, repo.path);
  if ("refusal" in where) return stop(where.refusal);
  const { cwd } = where;

  const { chain, label } = await chainOf(api, repo);
  const draft: FileDraft = { title, description, repo: repo.path, chain, ...paths, cwd };

  const dry = await api.preview(createBody(draft, false));
  const how = asked(await vscode.window.showQuickPick(
    [
      { label: "File paused", detail: "Nothing spends tokens until you start it.", start: false },
      { label: "File and start", start: true },
      { label: "More options…", detail: "Continue on Kraft's web page.", start: undefined },
    ],
    { title: previewLine(label, dry) },
  ));
  if (how.start === undefined) {
    const link = draftUrl(api.base, draft);
    if (link.briefDropped) await vscode.env.clipboard.writeText(description);
    // The string itself: Uri.parse would decode the query's %26 and %3D, and a title with & or = would split.
    await vscode.env.openExternal(link.url as unknown as vscode.Uri);
    const note = draftNote(link, repo.path);
    if (note) void vscode.window.showInformationMessage(note);
    return;
  }

  const c = await api.create(createBody(draft, how.start));
  await store.refresh(c.id).catch(() => {});
  await deps.reveal(c.id);
  void vscode.window.showInformationMessage(outcomeMessage(how.start, c));
  const warnings = warningsOf(c);
  if (warnings.length) void offerWarnings(deps, c.id, warnings, warningActions(c));
}

/** Kraft: File Work Item…, and its Explorer and editor doors. */
export function registerFileWork(context: vscode.ExtensionContext, deps: FileWorkDeps): void {
  const warn = (msg: string) => void vscode.window.showWarningMessage(msg);
  const run = (seedOf: (arg?: unknown) => Seed | undefined) => async (arg?: unknown) => {
    const seed = seedOf(arg);
    if (!seed) return;
    try {
      await fileWork(deps, seed);
    } catch (e) {
      if (e instanceof Stop) return;
      void vscode.window.showErrorMessage(e instanceof ApiError ? e.detail : String(e));
    }
  };
  const asFile = (as: Kind) => (arg?: unknown): Seed => ({ uri: arg instanceof vscode.Uri ? arg : vscode.window.activeTextEditor?.document.uri, as });
  const commands: [string, (arg?: unknown) => Seed | undefined][] = [
    ["kraft.fileWork", () => ({})],
    ["kraft.fileAsSpec", asFile("spec")],
    ["kraft.fileAsPlan", asFile("plan")],
    ["kraft.fileSelection", () => {
      const editor = vscode.window.activeTextEditor;
      return editor ? { uri: editor.document.uri, selection: editor.document.getText(editor.selection) } : undefined;
    }],
  ];
  for (const [id, seedOf] of commands) {
    context.subscriptions.push(vscode.commands.registerCommand(id, mutating(id, deps.refusal, warn, run(seedOf))));
  }
}
