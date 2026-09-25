import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import * as vscode from "vscode";
import { deriveState } from "../../frontend/src/deriveState";
import { registerBoard } from "./board";
import { Api } from "./core/api";
import type { Announcer } from "./core/announcer";
import { baseUrl, locations, readToken, type Locations } from "./core/connection";
import { Store, wsSocket } from "./core/store";
import { registerConfigChecks } from "./config/check";
import { registerResolved } from "./config/resolved";
import { registerSchemas } from "./config/schema";
import { registerGates } from "./gates";
import { registerDiff } from "./review/diff";
import { registerComments } from "./review/comments";
import { registerFindings } from "./review/findings";
import { compatible } from "./core/version";

export interface KraftApi {
  store: Store;
  api: Api;
  readOnly(): boolean;
  locations: Locations;
  gates: { announcer: Announcer };
  review: ReturnType<typeof registerDiff> & ReturnType<typeof registerComments>;
  config: ReturnType<typeof registerConfigChecks>;
}

function read(path: string): string | undefined {
  try { return readFileSync(path, "utf8"); } catch { return undefined; }
}

export async function activate(context: vscode.ExtensionContext): Promise<KraftApi> {
  const loc = locations(process.env, homedir());
  const setting = vscode.workspace.getConfiguration("kraft").get<string>("url");
  const api = new Api(baseUrl(setting, process.env, read(join(loc.templatesDir, "access.yaml"))), readToken(loc.runDir));
  const store = new Store(api, wsSocket);
  const own = (context.extension.packageJSON as { version: string }).version;
  let readOnly = false;
  let runDir = loc.runDir;

  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left);
  const setStatus = (connected: boolean) => {
    void vscode.commands.executeCommand("setContext", "kraft.connected", connected);
    status.text = connected ? `$(check) Kraft${readOnly ? " (read-only)" : ""}` : "$(debug-disconnect) Kraft";
    status.show();
  };
  store.onConnection(async (connected) => {
    if (connected) {
      try {
        const health = await api.health();
        runDir = health.run_dir;
        readOnly = !compatible(own, health.version);
        if (readOnly) void vscode.window.showWarningMessage(`Kraft ${health.version} does not match this extension (${own}); actions are disabled until they match.`);
      } catch {
        readOnly = true;
      }
      void vscode.commands.executeCommand("setContext", "kraft.readOnly", readOnly);
    }
    setStatus(connected);
  });
  setStatus(false);

  registerBoard(context, store, api, () => runDir, () => readOnly);
  context.subscriptions.push(status, { dispose: () => store.stop() });
  const comments = registerComments(context, store, api, () => readOnly);
  const gates = registerGates(context, store, api, () => readOnly, comments.confirmApprove);
  const diff = registerDiff(context, store, api);
  const review = Object.assign(diff, comments);
  registerFindings(context, store, diff.worktrees, () => runDir);
  // Details are fetched per event; an item already waiting when VS Code starts needs one fetch.
  const offAll = store.onChange((ids) => {
    if (ids !== "all") return;
    offAll();
    for (const i of store.items()) if (deriveState(i).needsYou) void store.refresh(i.id).catch(() => {});
  });
  void registerSchemas(context, loc.templatesDir);
  const config = registerConfigChecks(context, api, loc.templatesDir, () => readOnly);
  registerResolved(context, api, loc.templatesDir);
  void store.start();
  return { store, api, readOnly: () => readOnly, locations: loc, gates, review, config };
}

export function deactivate() {}
