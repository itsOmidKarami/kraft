import * as vscode from "vscode";
import { ApiError, type Api, type ItemAction, type WorkItem } from "./core/api";
import { steerPrompt } from "./core/board";
import { parseUsd } from "./core/budget";
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
      case "archive": await api.archive(item.id); break;
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
    await store.refresh(item.id);
  } catch (e) {
    await reportError(e, store, item.id);
  }
}
