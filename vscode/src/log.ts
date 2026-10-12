import * as vscode from "vscode";

let channel: vscode.LogOutputChannel | undefined;

/** The "Kraft" output channel. Calls before `initLog` are dropped. */
export const log = {
  info: (msg: string) => channel?.info(msg),
  warn: (msg: string) => channel?.warn(msg),
  error: (msg: string) => channel?.error(msg),
  show: () => channel?.show(true),
};

export function initLog(context: vscode.ExtensionContext): void {
  channel = vscode.window.createOutputChannel("Kraft", { log: true });
  context.subscriptions.push(channel, { dispose: () => (channel = undefined) });
}
