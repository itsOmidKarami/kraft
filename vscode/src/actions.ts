import * as vscode from "vscode";
import { ApiError, type Api, type BulkResult, type ItemAction, type WorkItem } from "./core/api";
import { steerPrompt } from "./core/board";
import { parseUsd } from "./core/budget";
import { bulkPlan, bulkSummary, type BulkVerb } from "./core/bulk";
import type { Store } from "./core/store";
import { log } from "./log";

export async function reportError(e: unknown, store: Store, id: string): Promise<void> {
  const detail = e instanceof ApiError ? e.detail : String(e);
  // A 409 is the item having moved on: worth a line, not an error.
  if (e instanceof ApiError && e.status === 409) log.info(`${id}: ${detail}`);
  else log.error(`${id}: ${detail}`);
  if (e instanceof ApiError && e.status === 409) {
    await store.refresh(id).catch(() => {});
    void vscode.window.showInformationMessage(e.detail);
    return;
  }
  void vscode.window.showErrorMessage(detail);
}

export async function runAction(api: Api, store: Store, item: WorkItem, action: ItemAction): Promise<void> {
  try {
    switch (action) {
      case "pause": await api.pause(item.id); break;
      case "archive": {
        // Archiving deletes the worktree, as the bulk one says; with none on disk there is nothing to lose.
        if (item.worktree_exists) {
          const ok = await vscode.window.showWarningMessage(`Archive ${item.title}? Its worktree is deleted.`, { modal: true }, "Archive");
          if (ok !== "Archive") return;
        }
        await api.archive(item.id);
        break;
      }
      case "unblock": await api.unblock(item.id); break;
      case "restore": await api.restore(item.id); break;
      case "resume":
      case "retry": {
        const steer = await vscode.window.showInputBox({ prompt: steerPrompt(item) });
        if (steer === undefined) return;
        await (action === "resume" ? api.resume(item.id, steer || undefined) : api.retry(item.id, steer || undefined));
        break;
      }
      case "cancel":
      case "complete": {
        const ask = action === "cancel"
          ? { prompt: `Why cancel ${item.id}? (recorded on the item)`, empty: "Cancelling needs a reason." }
          : { prompt: `Why mark ${item.id} complete? (recorded on the item)`, empty: "Completing needs a reason." };
        const reason = await vscode.window.showInputBox({
          prompt: ask.prompt,
          validateInput: (v) => (v.trim() ? undefined : ask.empty),
        });
        if (!reason?.trim()) return;
        await (action === "cancel" ? api.cancel(item.id, reason.trim()) : api.complete(item.id, reason.trim()));
        break;
      }
      case "raise_budget": {
        const why = item.stop?.reason ? `${item.stop.reason}. ` : "";
        const raw = await vscode.window.showInputBox({
          prompt: `${why}New spend cap for ${item.id} in dollars, or none`,
          validateInput: (v) => {
            const parsed = parseUsd(v);
            return "error" in parsed ? parsed.error : undefined;
          },
        });
        if (raw === undefined) return;
        const parsed = parseUsd(raw);
        if ("error" in parsed) return;
        await api.raiseBudget(item.id, parsed.usd);
        break;
      }
      case "skip": {
        const ok = await vscode.window.showWarningMessage(`Skip ${item.id}'s current step?`, { modal: true }, "Skip");
        if (ok !== "Skip") return;
        await api.skip(item.id);
        break;
      }
      case "escalate": {
        const message = await vscode.window.showInputBox({ prompt: `What should Kraft-Agent help with on ${item.id}?` });
        if (!message) return;
        await api.escalate(item.id, message);
        break;
      }
    }
    // The action went through: a failed re-read must not read as a failed action, inviting a second Skip.
    await store.refresh(item.id).catch(() => {});
  } catch (e) {
    await reportError(e, store, item.id);
  }
}

/** A pause, resume, archive or restore of several items: one summary for all
 *  of them. Resume sends no steer, and goes one id at a time, in order, since
 *  the server has no bulk resume. */
export async function runBulk(api: Api, store: Store, verb: BulkVerb, items: WorkItem[]): Promise<void> {
  const { ids, skipped } = bulkPlan(verb, items);
  if (verb === "archive" && ids.length > 1) {
    const ok = await vscode.window.showWarningMessage(`Archive ${ids.length} items? Their worktrees are deleted.`, { modal: true }, "Archive");
    if (ok !== "Archive") return;
  }
  const results: BulkResult[] = [];
  try {
    if (verb === "resume") {
      for (const id of ids) {
        try {
          await api.resume(id);
          results.push({ id, ok: true });
        } catch (e) {
          results.push({ id, ok: false, error: e instanceof ApiError ? e.detail : String(e) });
        }
      }
    } else if (ids.length) {
      await api.bulk(verb, ids, results);
    }
  } catch (e) {
    // A batch failed: the ones before it went through, and the server may have
    // acted on some of this one, so the refresh below still reads each id.
    const detail = e instanceof ApiError ? e.detail : String(e);
    log.error(`${verb} ${ids.join(", ")}: ${detail}`);
    const answered = new Set(results.map((r) => r.id));
    results.push(...ids.filter((id) => !answered.has(id)).map((id) => ({ id, ok: false, error: detail })));
  } finally {
    await Promise.all(ids.map((id) => store.refresh(id).catch(() => {})));
  }
  const line = bulkSummary(verb, results, skipped);
  log.info(line);
  if (skipped.length || results.some((r) => !r.ok)) void vscode.window.showWarningMessage(line);
  else void vscode.window.showInformationMessage(line);
}
