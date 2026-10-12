import { execFile } from "node:child_process";
import { homedir } from "node:os";
import * as vscode from "vscode";
import { reportError, runAction, runBulk } from "./actions";
import { ApiError, type Api, type ItemAction, type WorkItem } from "./core/api";
import { archivedChildren, contextValueOf, hoverFromCache, describe, groupHolding, groupItems, iconOf, needsYouCount, onBoard, tooltipOf, welcomeState, withArchived, worktreeTarget, type Include, type Welcome } from "./core/board";
import { selectedItems, type BulkVerb, type SelectionRow } from "./core/bulk";
import { findingsOf } from "./core/findings";
import { downRow } from "./core/health";
import { mutating } from "./core/guard";
import type { PickVerb } from "./core/picks";
import { rowCommand } from "./core/primary";
import { RESTRICTED } from "./core/readOnly";
import { ALREADY_STARTING, alreadyRunning, oneAtATime, startFailure, startInvocation, type StartError } from "./core/start";
import type { WorkspaceScope } from "./core/repoScope";
import type { Store } from "./core/store";
import { log } from "./log";
import { pickItem } from "./palette";

type Node =
  | { kind: "group"; id: string; label: string; items: WorkItem[] }
  | { kind: "item"; item: WorkItem }
  | { kind: "findings"; item: WorkItem }
  | { kind: "finding"; text: string }
  | { kind: "down" }
  | { kind: "archived" }
  | { kind: "note"; text: string };

const ARCHIVED: Node = { kind: "archived" };
// The events after which the Archived group's rows are read again.
const ARCHIVE_MOVES = new Set(["work_item_archived", "work_item_restored"]);

export class Board implements vscode.TreeDataProvider<Node> {
  private changed = new vscode.EventEmitter<Node | void>();
  readonly onDidChangeTreeData = this.changed.event;

  private welcome: Welcome | undefined;
  private draftsTimer?: ReturnType<typeof setTimeout>;
  // The Archived group's last read: its rows, or why it failed; undefined before the first.
  private archived: { rows: WorkItem[] } | { error: string } | undefined;
  // Bumped by every read of it: a reply a newer read overtook does nothing.
  private archivedGen = 0;
  private archivedTimer?: ReturnType<typeof setTimeout>;

  constructor(
    private store: Store,
    private include: Include,
    private scope: Pick<WorkspaceScope, "onChange" | "ready" | "showAll">,
    private drafts?: Drafts,
    /** Whether "Connect this repo" applies: a folder's repo is not connected, on a loopback daemon. */
    private unconnected: () => boolean = () => false,
    private listArchived: () => Promise<WorkItem[]> = async () => [],
  ) {
    // Coalesced: a reconnect preloads every gate item, one answer at a time.
    drafts?.onChange(() => {
      clearTimeout(this.draftsTimer);
      this.draftsTimer = setTimeout(() => this.changed.fire(), 100);
    });
    store.onChange(() => this.changed.fire());
    store.onConnection((up) => {
      this.changed.fire();
      if (up) void this.loadArchived();
    });
    // Coalesced: a bulk archive or restore sends one event per item.
    store.onEvent((ev) => {
      if (!ARCHIVE_MOVES.has(ev.type)) return;
      clearTimeout(this.archivedTimer);
      this.archivedTimer = setTimeout(() => void this.loadArchived(), 100);
    });
    store.onError(() => this.changed.fire());
    scope.onChange(() => this.changed.fire());
  }

  refresh = () => this.changed.fire();

  dispose(): void {
    clearTimeout(this.archivedTimer);
    clearTimeout(this.draftsTimer);
  }

  /** Read the Archived group's rows again. Only on its expand, a connect and
   *  an archive or restore: drawing the group never reads. */
  async loadArchived(): Promise<void> {
    const gen = ++this.archivedGen;
    let next: { rows: WorkItem[] } | { error: string };
    try {
      next = { rows: await this.listArchived() };
    } catch (e) {
      next = { error: e instanceof ApiError ? e.detail : e instanceof Error ? e.message : String(e) };
    }
    if (gen !== this.archivedGen) return;
    this.archived = next;
    this.changed.fire();
  }

  private archivedNodes(): Node[] {
    const a = this.archived;
    if (!a) return [];
    if ("error" in a) return [{ kind: "note", text: `Couldn't load archived items: ${a.error}` }];
    return archivedChildren(a.rows, this.store).filter(this.include).map((item) => ({ kind: "item", item }));
  }

  private unlocated(item: WorkItem) {
    const d = this.store.detail(item.id);
    return d ? findingsOf(d).unlocated : [];
  }

  getChildren(node?: Node): Node[] {
    if (node?.kind === "item") return this.unlocated(node.item).length ? [{ kind: "findings", item: node.item }] : [];
    if (node?.kind === "findings") return this.unlocated(node.item).map((f) => ({ kind: "finding", text: `${f.severity}: ${f.message}` }));
    if (node?.kind === "group") return node.items.map((item) => ({ kind: "item", item }));
    if (node?.kind === "archived") return this.archivedNodes();
    if (node) return [];
    const groups = groupItems(this.store.items(), this.include);
    const visible = groups.reduce((n, g) => n + g.items.length, 0);
    this.setWelcome(welcomeState(this.store.connected, this.scope.ready, this.scope.showAll, visible, this.unconnected()));
    if (!this.store.connected && visible === 0) return [{ kind: "down" }];
    return withArchived<Node>(groups.map((g) => ({ kind: "group", ...g })));
  }

  /** Only an item has a parent here, the group holding it, or Archived for
   *  an archived one: `reveal` needs no more. */
  getParent(node: Node): Node | undefined {
    if (node.kind === "note") return ARCHIVED;
    if (node.kind !== "item") return undefined;
    if (node.item.archived_at) return ARCHIVED;
    const g = groupHolding(this.store.items(), this.include, node.item.id);
    return g && { kind: "group", ...g };
  }

  private setWelcome(w: Welcome) {
    if (w === this.welcome) return;
    this.welcome = w;
    void vscode.commands.executeCommand("setContext", "kraft.welcome", w);
  }

  getTreeItem(node: Node): vscode.TreeItem {
    if (node.kind === "down") {
      const { label, command, args } = downRow(this.store.lastError);
      const t = new vscode.TreeItem(label);
      t.iconPath = new vscode.ThemeIcon("debug-disconnect");
      t.command = { command, title: label, arguments: args };
      return t;
    }
    if (node.kind === "group") {
      const t = new vscode.TreeItem(`${node.label} (${node.items.length})`, vscode.TreeItemCollapsibleState.Expanded);
      // Its key, not its label: the count in the label changes as items move.
      t.id = `group:${node.id}`;
      t.contextValue = "group";
      return t;
    }
    if (node.kind === "archived") {
      // No count: its rows are read only on expand, a connect and an archive or restore.
      const t = new vscode.TreeItem("Archived", vscode.TreeItemCollapsibleState.Collapsed);
      t.id = "group:archived";
      t.contextValue = "archived";
      return t;
    }
    if (node.kind === "note") return new vscode.TreeItem(node.text);
    if (node.kind === "findings") {
      const t = new vscode.TreeItem(`Findings (${this.unlocated(node.item).length})`, vscode.TreeItemCollapsibleState.Collapsed);
      t.iconPath = new vscode.ThemeIcon("warning");
      return t;
    }
    if (node.kind === "finding") return new vscode.TreeItem(node.text);
    const { item } = node;
    const t = new vscode.TreeItem(item.title, this.unlocated(item).length ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None);
    t.id = item.id;
    t.description = describe(item);
    t.iconPath = new vscode.ThemeIcon(iconOf(item));
    t.contextValue = contextValueOf(item, { drafts: this.drafts?.has(item.id) ?? false });
    // A gate row's click opens its document; any other row's only selects it.
    const click = rowCommand(item);
    if (click) t.command = { ...click, title: "Open", arguments: [{ item }] };
    // VS Code asks resolveTreeItem only for what is left unset here: without a
    // cached detail the hover is filled in there, on first hover.
    const detail = this.store.detail(item.id);
    if (detail && hoverFromCache(item)) t.tooltip = tooltipOf(item, detail);
    return t;
  }

  /** An item's hover, once it has the detail that carries spend and running
   *  time. The read is `loadDetail`, which redraws nothing; a failed one keeps
   *  the hover quiet: it shows the row's own line. */
  async resolveTreeItem(t: vscode.TreeItem, node: Node): Promise<vscode.TreeItem> {
    if (node.kind !== "item" || t.tooltip !== undefined) return t;
    const item = this.store.item(node.item.id) ?? node.item;
    try {
      t.tooltip = tooltipOf(item, await this.store.loadDetail(item.id));
    } catch {
      t.tooltip = tooltipOf(item);
    }
    return t;
  }

  badge(): vscode.ViewBadge | undefined {
    const n = needsYouCount(this.store.items(), this.include);
    return n ? { value: n, tooltip: `${n} waiting on you` } : undefined;
  }
}

/** Which items have draft review comments, as far as their loaded threads say. */
export interface Drafts { has(id: string): boolean; onChange(cb: (id: string) => void): unknown }

export function registerBoard(
  context: vscode.ExtensionContext,
  store: Store,
  api: Api,
  refusal: () => string | null,
  include: Include,
  scope: Pick<WorkspaceScope, "onChange" | "ready" | "showAll">,
  drafts?: Drafts,
  unconnected?: () => boolean,
) {
  const board = new Board(store, include, scope, drafts, unconnected, async () => (await api.listArchived()).items);
  const view = vscode.window.createTreeView("kraft.board", { treeDataProvider: board, canSelectMany: true });
  // Every expand reads it again: the cache may be as old as the last connect.
  context.subscriptions.push(board, view.onDidExpandElement((e) => {
    if (e.element.kind === "archived") void board.loadArchived();
  }));
  const updateBadge = () => (view.badge = board.badge());
  store.onChange(updateBadge);
  scope.onChange(updateBadge);

  const itemOf = (node: { item?: WorkItem } | undefined) => node?.item;
  // From the palette there is no row: ask for one among the window's items,
  // or, for Restore, among its archived ones.
  const inScope = () => onBoard(store.items(), include);
  const pickFor = async (verb: PickVerb): Promise<WorkItem | undefined> => {
    if (verb !== "restore") return pickItem(verb, inScope());
    let archived: WorkItem[];
    try {
      archived = archivedChildren((await api.listArchived()).items, store).filter(include);
    } catch (e) {
      return void vscode.window.showErrorMessage(`Couldn't load archived items: ${e instanceof ApiError ? e.detail : String(e)}`);
    }
    return pickItem(verb, archived);
  };
  const itemOr = async (node: { item?: WorkItem } | undefined, verb: PickVerb) => itemOf(node) ?? pickFor(verb);
  const act = (action: ItemAction, verb: PickVerb) => async (node?: { item?: WorkItem }) => {
    const item = await itemOr(node, verb);
    if (!item) return;
    await runAction(api, store, item, action);
  };
  const warn = (msg: string) => void vscode.window.showWarningMessage(msg);
  // The board's own doors; approve and reject are registered in gates.ts, and
  // steer, reopen_mr and abandon have no command here.
  const COMMANDS: Partial<Record<ItemAction, string>> = {
    pause: "kraft.pause", resume: "kraft.resume", retry: "kraft.retry", raise_budget: "kraft.raiseBudget",
    unblock: "kraft.unblock", skip: "kraft.skip", escalate: "kraft.escalate", complete: "kraft.complete",
    cancel: "kraft.cancel", restore: "kraft.restore", archive: "kraft.archive",
  };
  // Only these four read the selection VS Code passes second; every other
  // command acts on the clicked row alone. One selected row keeps its own path.
  const BULK: ReadonlySet<ItemAction> = new Set<BulkVerb>(["pause", "resume", "archive", "restore"]);
  const actOnSelection = (a: BulkVerb) => async (node?: SelectionRow, nodes?: readonly SelectionRow[]) => {
    const items = selectedItems(node, nodes);
    if (items.length < 2) return act(a, a)(node);
    await runBulk(api, store, a, items.map((i) => store.item(i.id) ?? i));
  };
  for (const [a, id] of Object.entries(COMMANDS) as [ItemAction, string][]) {
    const run = BULK.has(a) ? actOnSelection(a as BulkVerb) : act(a, id.slice("kraft.".length) as PickVerb);
    context.subscriptions.push(vscode.commands.registerCommand(id, mutating(id, refusal, warn, run)));
  }
  context.subscriptions.push(
    view,
    // A row's Answer button: a resume whose input box shows the question. The
    // clicked row only, never the selection.
    vscode.commands.registerCommand("kraft.answer", mutating("kraft.answer", refusal, warn, act("resume", "answer"))),
    // The detail, refreshed now: the row's worktree_exists may predate a reclaim.
    vscode.commands.registerCommand("kraft.openWorktree", async (node?: { item?: WorkItem }) => {
      const id = (await itemOr(node, "openWorktree"))?.id;
      if (!id) return;
      try {
        await store.refresh(id);
      } catch (e) {
        return reportError(e, store, id);
      }
      const target = worktreeTarget(store.detail(id));
      if (!target) return void vscode.window.showInformationMessage(`${id} has no worktree on disk: it was reclaimed, or the item has not run yet.`);
      await vscode.commands.executeCommand("vscode.openFolder", vscode.Uri.file(target), { forceNewWindow: true });
    }),
    vscode.commands.registerCommand("kraft.openInWebUi", async (node?: { item?: WorkItem }) => {
      const item = await itemOr(node, "openInWebUi");
      if (item) void vscode.env.openExternal(vscode.Uri.parse(`${api.base}/work-items/${encodeURIComponent(item.id)}`));
    }),
    // Only Restricted Mode refuses: a version mismatch must not stop starting Kraft.
    // From home, so the workspace's venv or shims cannot pick the `kraft` binary; detached,
    // so it needs no terminal. One start at a time: a second click says so.
    vscode.commands.registerCommand("kraft.startDaemon", mutating("kraft.startDaemon", () => (refusal() === RESTRICTED ? RESTRICTED : null), warn, () => {
      const { file, args } = startInvocation(vscode.workspace.getConfiguration("kraft").get<string>("path"), homedir());
      return startOnce(() => vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Starting Kraft…" }, () => startKraft(file, args, store)));
    })),
  );
  /** Selects an item on the board, without taking focus. False when the board
   *  does not show it (unknown, archived, or outside the scope), so a caller can
   *  open it somewhere else; that is no error. */
  const reveal = async (id: string): Promise<boolean> => {
    const item = store.item(id);
    if (!item || !groupHolding(store.items(), include, id)) return false;
    try {
      await view.reveal({ kind: "item", item }, { select: true, focus: false });
    } catch {
      // The view may not be resolved yet; the item is on the board either way.
    }
    return true;
  };
  return { board, reveal, inScope };
}

const startOnce = oneAtATime(() => void vscode.window.showInformationMessage(ALREADY_STARTING));

/** Runs `kraft admin start --detach`, which returns once the server answers, then reconnects. */
async function startKraft(file: string, args: string[], store: Store): Promise<void> {
  try {
    await new Promise<void>((ok, fail) =>
      execFile(file, args, { cwd: homedir(), timeout: 30_000 }, (err, _stdout, stderr) =>
        err ? fail(Object.assign(err, { stderr })) : ok()));
    store.retryNow();
  } catch (e) {
    const err = e as StartError;
    const stderr = String(err.stderr ?? "").trim();
    log.warn(`Start Kraft: ${file} ${args.join(" ")} failed${stderr ? `:\n${stderr}` : `: ${err.message}`}`);
    if (alreadyRunning(err)) {
      void vscode.window.showInformationMessage(startFailure(err, file));
      return store.retryNow();
    }
    const settings = err.code === "ENOENT" ? ["Open Settings"] : [];
    // Not awaited: an open toast must not hold the progress notification or the next start.
    void vscode.window.showErrorMessage(startFailure(err, file), ...settings).then((pick) => {
      if (pick) void vscode.commands.executeCommand("workbench.action.openSettings", "kraft.path");
    });
  }
}
