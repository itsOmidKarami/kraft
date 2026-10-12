import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import * as vscode from "vscode";
import { deriveState } from "../../frontend/src/deriveState";
import { registerBoard } from "./board";
import { Api } from "./core/api";
import type { Announcer } from "./core/announcer";
import { connectionFor, locations, readToken, type Locations } from "./core/connection";
import { Store, wsSocket } from "./core/store";
import { registerConfigChecks } from "./config/check";
import { registerResolved } from "./config/resolved";
import { registerSchemas } from "./config/schema";
import { registerGates } from "./gates";
import { registerDiff } from "./review/diff";
import { registerComments } from "./review/comments";
import { registerFindings } from "./review/findings";
import { readOnlyReason } from "./core/version";
import { refusal, tooltip, whenTrusted, type ReadOnlyState } from "./core/readOnly";

export interface KraftApi {
  store: Store;
  api: Api;
  readOnly(): boolean;
  locations: Locations;
  gates: { announcer: Announcer };
  review: ReturnType<typeof registerDiff> & ReturnType<typeof registerComments>;
  // Absent until the workspace is trusted.
  readonly config: ReturnType<typeof registerConfigChecks> | undefined;
}

function read(path: string): string | undefined {
  try { return readFileSync(path, "utf8"); } catch { return undefined; }
}

export async function activate(context: vscode.ExtensionContext): Promise<KraftApi> {
  const loc = locations(process.env, homedir());
  const cfg = vscode.workspace.getConfiguration("kraft");
  const { base, token } = connectionFor({
    setting: cfg.get<string>("url"),
    globalSetting: cfg.inspect<string>("url")?.globalValue,
    env: process.env,
    accessYaml: read(join(loc.templatesDir, "access.yaml")),
    token: readToken(loc.runDir),
  });
  const api = new Api(base, token);
  const store = new Store(api, wsSocket);
  const own = (context.extension.packageJSON as { version: string }).version;
  let trusted = vscode.workspace.isTrusted;
  let version: string | null = null;
  const state = (): ReadOnlyState => ({ trusted, version });
  let connected = false;
  let runDir = loc.runDir;

  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left);
  const setStatus = (now: boolean) => {
    connected = now;
    void vscode.commands.executeCommand("setContext", "kraft.connected", connected);
    status.text = connected ? `$(check) Kraft${refusal(state()) ? " (read-only)" : ""}` : "$(debug-disconnect) Kraft";
    status.tooltip = connected ? tooltip(state()) : undefined;
    status.show();
  };
  const applyReadOnly = (now = connected) => {
    void vscode.commands.executeCommand("setContext", "kraft.readOnly", refusal(state()) !== null);
    setStatus(now);
  };
  store.onConnection(async (connected) => {
    if (connected) {
      try {
        const health = await api.health();
        runDir = health.run_dir;
        version = readOnlyReason(own, health);
        if (version) void vscode.window.showWarningMessage(version);
      } catch {
        version = readOnlyReason(own, null);
      }
    }
    applyReadOnly(connected);
  });
  // Before the first connect: a restored gate tab must not offer Approve in Restricted Mode.
  applyReadOnly();

  registerBoard(context, store, api, () => runDir, () => refusal(state()));
  context.subscriptions.push(status, { dispose: () => store.stop() });
  const comments = registerComments(context, store, api, () => refusal(state()));
  const gates = registerGates(context, store, api, () => refusal(state()), comments.confirmApprove);
  const diff = registerDiff(context, store, api, () => runDir);
  const review = Object.assign(diff, comments);
  registerFindings(context, store);
  // Details are fetched per event; connect clears them, so every "all" (start and reconnect) refetches for items waiting on a person.
  store.onChange((ids) => {
    if (ids !== "all") return;
    for (const i of store.items()) if (deriveState(i).needsYou) void store.refresh(i.id).catch(() => {});
  });
  // Config checks, schemas and the resolved chain wait for trust; no reload needed.
  let config: ReturnType<typeof registerConfigChecks> | undefined;
  const grant = whenTrusted(trusted, (l) => vscode.workspace.onDidGrantWorkspaceTrust(l), () => {
    trusted = true;
    void registerSchemas(context, loc.templatesDir);
    config = registerConfigChecks(context, api, loc.templatesDir, () => refusal(state()) !== null);
    registerResolved(context, api, loc.templatesDir);
    applyReadOnly();
  });
  if (grant) context.subscriptions.push(grant);
  void store.start();
  return { store, api, readOnly: () => refusal(state()) !== null, locations: loc, gates, review, get config() { return config; } };
}

export function deactivate() {}
