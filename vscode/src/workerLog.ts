import * as vscode from "vscode";
import { reportError } from "./actions";
import type { Api, WorkItem } from "./core/api";
import { followLog, formatLogLine, logSession } from "./core/logStream";
import type { Store } from "./core/store";
import { pickItem } from "./palette";

interface Open { terminal?: vscode.Terminal; abort: AbortController }

/** `kraft.showWorkerLog`: the item's session in a terminal, followed until it ends. A read, so unguarded. */
export function registerWorkerLog(context: vscode.ExtensionContext, store: Store, api: Api, inScope: () => WorkItem[]): void {
  // One terminal per item; a re-run replaces it.
  const open = new Map<string, Open>();
  context.subscriptions.push(
    { dispose: () => { for (const o of open.values()) o.abort.abort(); open.clear(); } },
    vscode.commands.registerCommand("kraft.showWorkerLog", async (node?: { item?: WorkItem }) => {
      const id = (node?.item ?? (await pickItem("showWorkerLog", inScope())))?.id;
      if (!id) return;
      try {
        await store.refresh(id);
      } catch (e) {
        return reportError(e, store, id);
      }
      const session = logSession(store.detail(id));
      if (!session) return void vscode.window.showInformationMessage(`No worker session has run for ${id} yet.`);

      const earlier = open.get(id);
      earlier?.abort.abort();
      earlier?.terminal?.dispose();
      const me: Open = { abort: new AbortController() };
      const out = new vscode.EventEmitter<string>();
      const say = (s: string) => out.fire(`${s}\r\n`);
      const run = async () => {
        let lost = "";
        const end = await followLog((signal) => api.streamLog(session.work_item_id, session.id, signal), (l) => say(formatLogLine(l)), me.abort.signal, {
          onLost: (d) => (lost = d),
        });
        if (end === "aborted") return;
        if (end === "lost") return say(`— stream lost: ${lost} —`);
        let status: string = session.status;
        try {
          status = (await api.getItem(id)).worker_sessions?.find((s) => s.id === session.id)?.status ?? status;
        } catch { /* the status it had when the stream opened */ }
        if (!me.abort.signal.aborted) say(`— session ${status} —`);
      };
      me.terminal = vscode.window.createTerminal({
        name: `Kraft: ${id}`,
        pty: {
          onDidWrite: out.event,
          open: () => void run(),
          close: () => {
            me.abort.abort();
            out.dispose();
            if (open.get(id) === me) open.delete(id);
          },
        },
      });
      open.set(id, me);
      me.terminal.show();
    }),
  );
}
