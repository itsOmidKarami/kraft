import * as vscode from "vscode";
import { ApiError, type Api } from "../core/api";
import { dropMessage, migrateAll, type LegacyDraft, type MigrateDeps } from "../core/migrate";
import type { ThreadCache } from "../core/threadCache";

const ENDED = new Set(["done", "cancelled", "archived"]);

/**
 * Moves the 1.x extension's local review drafts onto the server, one run at a
 * time. extension.ts runs it on each connect, once that connection's read-only
 * state is known, and when trust is granted.
 */
export function registerMigration(context: vscode.ExtensionContext, api: Api, refusal: () => string | null, cache: ThreadCache, name: (id: string) => string = (id) => id) {
  const deps: MigrateDeps = {
    reviewable: async (id) => {
      try {
        const item = await api.getItem(id);
        if (ENDED.has(item.display_status ?? "")) return false;
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return false;
        throw e;
      }
      try {
        return !!(await api.getDiff(id)).base_ref;
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return false; // its worktree is gone
        throw e;
      }
    },
    existing: async (id) => (await api.listThreads(id)).map((t) => t.comments[0]?.body ?? ""),
    post: async (id, t) => void (await api.createThread(id, t)),
    save: async (id, d) => context.workspaceState.update(`kraft.draft.${id}`, d),
    writable: () => refusal() === null,
  };

  let running = false;
  let again = false; // asked for during a run: one more after it
  const run = async (): Promise<void> => {
    if (running) return void (again = true);
    running = true;
    again = false;
    try {
      const results = await migrateAll(context.workspaceState.keys(), (k) => context.workspaceState.get<LegacyDraft>(k), deps, refusal() !== null);
      for (const r of results) {
        if (r.posted) {
          void cache.load(r.id).catch(() => {});
          const s = r.posted === 1 ? "" : "s";
          void vscode.window
            .showInformationMessage(`Kraft moved ${r.posted} local review comment${s} on ${name(r.id)} to its review, as drafts.`, "Review Changes")
            .then((pick) => pick && vscode.commands.executeCommand("kraft.reviewChanges", r.id));
        }
        if (r.dropped) {
          const text = dropMessage(name(r.id), r.dropped, r.why);
          void vscode.window.showWarningMessage(text, "Copy").then((pick) => pick && vscode.env.clipboard.writeText(text.split("\n").slice(1).join("\n")));
        }
      }
    } finally {
      running = false;
    }
    if (again) return run();
  };
  return { migrate: run };
}
