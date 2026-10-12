import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import * as vscode from "vscode";
import { registerBoard } from "./board";
import { Api, type WorkItem } from "./core/api";
import type { BudgetToday } from "../../frontend/src/types";
import type { Announcer } from "./core/announcer";
import { needsYou } from "./core/board";
import { registerConnectRepo, registerRepoScope, scopeDeps } from "./repoScope";
import { registerFileWork } from "./fileWork";
import { registerWorkerLog } from "./workerLog";
import { registerDocs } from "./docs";
import { registerSearch } from "./search";
import { connectOffered, connectRefusal, fileRefusal } from "./core/fileWork";
import { connectionFor, locations, readToken, type Locations } from "./core/connection";
import { Store, wsSocket } from "./core/store";
import { ThreadCache } from "./core/threadCache";
import { hasDrafts } from "./core/threads";
import { registerConfigChecks } from "./config/check";
import { registerResolved } from "./config/resolved";
import { registerSchemas } from "./config/schema";
import { registerGates } from "./gates";
import { registerDiff } from "./review/diff";
import { registerComments } from "./review/comments";
import { registerReviewSubmit } from "./review/submit";
import { registerMigration } from "./review/migrate";
import { registerFindings } from "./review/findings";
import { readOnlyReason } from "./core/version";
import { refusal, tooltip, whenTrusted, type ReadOnlyState } from "./core/readOnly";
import { acceptHealth, every, onceEach, statusMenu, statusTooltip } from "./core/health";
import { initLog, log } from "./log";

export interface KraftApi {
  store: Store;
  api: Api;
  readOnly(): boolean;
  locations: Locations;
  gates: { announcer: Announcer };
  review: ReturnType<typeof registerDiff> & ReturnType<typeof registerComments> & ReturnType<typeof registerReviewSubmit>;
  // Absent until the workspace is trusted.
  readonly config: ReturnType<typeof registerConfigChecks> | undefined;
}

function read(path: string): string | undefined {
  try { return readFileSync(path, "utf8"); } catch { return undefined; }
}

export async function activate(context: vscode.ExtensionContext): Promise<KraftApi> {
  initLog(context);
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
  api.onError = (f) => {
    // The store logs a failed list call itself, once per distinct failure, not on every retry.
    // The Archived group's read is not that call: its failure is logged here, and shown as a row.
    if (f.method === "GET" && f.path.startsWith("/work-items?") && !f.path.includes("archived=true")) return;
    const line = `${f.method} ${f.path}: ${f.status} ${f.detail}`;
    // Gone, or moved on before the call landed: expected, not an error.
    // Today's spend is a hover nicety: an older server without the route, or a blip, is not an error.
    // A query search cannot parse (a stray quote, say) is the person still typing.
    if (f.status === 404 || f.status === 409 || f.path === "/budget/today" || (f.status === 422 && f.path.startsWith("/search?"))) log.info(line);
    else log.error(line);
  };
  const store = new Store(api, wsSocket, (level, msg) => log[level](msg));
  const own = (context.extension.packageJSON as { version: string }).version;
  let trusted = vscode.workspace.isTrusted;
  let version: string | null = null;
  const state = (): ReadOnlyState => ({ trusted, version });
  let connected = false;
  let runDir = loc.runDir;
  // Before the board: its first render already asks the scope.
  const repoScope = registerRepoScope(context, runDir, api, store);
  const { scope } = repoScope;
  const include = (i: WorkItem) => scope.includes(i, (id) => store.item(id)?.repo);

  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left);
  const setStatus = (now: boolean) => {
    connected = now;
    void vscode.commands.executeCommand("setContext", "kraft.connected", connected);
    status.text = connected ? `$(check) Kraft${refusal(state()) ? " (read-only)" : ""}` : "$(debug-disconnect) Kraft";
    status.tooltip = statusTooltip(connected, tooltip(state()), store.lastError, today);
    status.command = "kraft.statusMenu";
    status.show();
  };
  const applyReadOnly = (now = connected) => {
    void vscode.commands.executeCommand("setContext", "kraft.readOnly", refusal(state()) !== null);
    setStatus(now);
  };
  // Today's spend for the hover: the last answer, cleared by a failure or a drop. Each
  // read bumps `todaySeq`, so only the latest one lands, and only if `fresh` still holds.
  let today: BudgetToday | null = null;
  let todaySeq = 0;
  const todayDue = every(30_000);
  const loadToday = async (fresh: () => boolean) => {
    const mine = ++todaySeq;
    const next = await api.budgetToday().catch(() => null);
    if (mine !== todaySeq || !fresh()) return;
    today = next;
    setStatus(store.connected);
  };
  // One toast per distinct reason a session: a flapping connection must not repeat it.
  const warnOnce = onceEach();
  // Each connect bumps it: a health reply that a newer connect or a drop overtook changes nothing.
  let healthSeq = 0;
  let migrate: (() => Promise<void>) | undefined; // set once the review is registered
  let healthSettled = false; // this connection's read-only state is known
  store.onConnection(async (connected) => {
    const mine = ++healthSeq;
    healthSettled = false;
    if (connected) {
      try {
        const health = await api.health();
        if (!acceptHealth(mine, healthSeq, store.connected)) return;
        runDir = health.run_dir;
        version = readOnlyReason(own, health);
        if (version && warnOnce(version)) void vscode.window.showWarningMessage(version);
        // Off the status bar's path.
        void repoScope.setDaemon(health.run_dir);
      } catch (e) {
        if (!acceptHealth(mine, healthSeq, store.connected)) return;
        log.warn(`health check failed, so the server's version is unknown: ${(e as Error).message}`);
        version = readOnlyReason(own, null);
      }
      // Reached only by an accepted connect: a stale one returned above.
      todayDue(true);
      void loadToday(() => acceptHealth(mine, healthSeq, store.connected));
    } else today = null;
    applyReadOnly(store.connected);
    // Only once this connection's read-only state is known: a read-only window posts nothing.
    if (connected && store.connected && mine === healthSeq) {
      healthSettled = true;
      void migrate?.();
    }
  });
  store.onError(() => setStatus(store.connected));
  // Before the first connect: a restored gate tab must not offer Approve in Restricted Mode.
  applyReadOnly();

  const cache = new ThreadCache(api);
  const { reveal } = registerBoard(context, store, api, () => refusal(state()), include, scope, {
    has: (id) => hasDrafts(cache.get(id)),
    onChange: (cb) => cache.onChange(cb),
  }, () => connectOffered(api.base, repoScope.unconnected()));
  registerConnectRepo(context, api, repoScope, () => connectRefusal(state(), api.base));
  registerWorkerLog(context, store, api);
  registerDocs(context, store, api);
  registerSearch(context, api, reveal);
  registerFileWork(context, { api, store, reveal, refusal: () => fileRefusal(state(), api.base), runDir: () => runDir, scope: scopeDeps });
  context.subscriptions.push(
    status,
    { dispose: () => store.stop() },
    vscode.commands.registerCommand("kraft.showLog", () => log.show()),
    vscode.commands.registerCommand("kraft.statusMenu", async () => {
      const pick = await vscode.window.showQuickPick(statusMenu(store.connected), { placeHolder: "Kraft" });
      switch (pick?.choice) {
        case "log": return log.show();
        case "web": return void vscode.env.openExternal(vscode.Uri.parse(api.base));
        // Guarded in board.ts: Restricted Mode refuses it.
        case "start": return void vscode.commands.executeCommand("kraft.startDaemon");
        case "retry": return store.retryNow();
      }
    }),
  );
  const diff = registerDiff(context, store, api, () => runDir);
  const comments = registerComments(context, store, api, () => refusal(state()), diff, cache, include, (cb) => scope.onChange(cb));
  migrate = registerMigration(context, api, () => refusal(state()), cache).migrate;
  const gates = registerGates(context, store, api, () => refusal(state()), include, scope, cache, comments.reveal);
  const submit = registerReviewSubmit(context, store, api, () => refusal(state()), cache, gates.shown, gates.show, gates.openArtifact, comments.reveal);
  const review = Object.assign(diff, comments, submit);
  registerFindings(context, store);
  // Details are fetched per event; connect clears them, so every "all" (start and reconnect) refetches for items waiting on a person.
  store.onChange((ids) => {
    if (store.connected && todayDue()) void loadToday(() => store.connected);
    if (ids !== "all") return;
    for (const i of store.items()) if (needsYou(i)) void store.refresh(i.id).catch(() => {});
  });
  // Config checks, schemas and the resolved chain wait for trust; no reload needed.
  let config: ReturnType<typeof registerConfigChecks> | undefined;
  const grant = whenTrusted(trusted, (l) => vscode.workspace.onDidGrantWorkspaceTrust(l), () => {
    trusted = true;
    void registerSchemas(context, loc.templatesDir);
    config = registerConfigChecks(context, api, loc.templatesDir, () => refusal(state()) !== null);
    registerResolved(context, api, loc.templatesDir);
    applyReadOnly();
    if (store.connected && healthSettled) void migrate?.();
  });
  if (grant) context.subscriptions.push(grant);
  void store.start();
  return { store, api, readOnly: () => refusal(state()) !== null, locations: loc, gates, review, get config() { return config; } };
}

export function deactivate() {}
