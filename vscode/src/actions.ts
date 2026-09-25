import * as vscode from "vscode";
import { ApiError, type Api, type WorkItem } from "./core/api";
import type { Action } from "./core/board";
import type { Store } from "./core/store";

export async function reportError(e: unknown, store: Store, id: string): Promise<void> {
  if (e instanceof ApiError && e.status === 409) {
    await store.refresh(id).catch(() => {});
    void vscode.window.showInformationMessage(e.detail);
    return;
  }
  void vscode.window.showErrorMessage(e instanceof ApiError ? e.detail : String(e));
}

export async function runAction(api: Api, store: Store, item: WorkItem, action: Action): Promise<void> {
  try {
    switch (action) {
      case "pause": await api.pause(item.id); break;
      case "archive": await api.archive(item.id); break;
      case "resume":
      case "retry": {
        const steer = await vscode.window.showInputBox({ prompt: `Steer for ${item.id} (optional)` });
        if (steer === undefined) return;
        await (action === "resume" ? api.resume(item.id, steer || undefined) : api.retry(item.id, steer || undefined));
        break;
      }
      case "cancel": {
        const reason = await vscode.window.showInputBox({
          prompt: `Why cancel ${item.id}? (recorded on the item)`,
          validateInput: (v) => (v.trim() ? undefined : "Cancelling needs a reason."),
        });
        if (!reason?.trim()) return;
        await api.cancel(item.id, reason.trim());
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
