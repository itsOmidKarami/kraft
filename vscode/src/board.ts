import { homedir } from "node:os";
import * as vscode from "vscode";
import { runAction } from "./actions";
import type { Api, ItemAction, WorkItem } from "./core/api";
import { contextValueOf, describe, groupItems, iconOf, needsYouCount, tooltipOf } from "./core/board";
import { findingsOf } from "./core/findings";
import { mutating } from "./core/guard";
import { RESTRICTED } from "./core/readOnly";
import type { Store } from "./core/store";

type Node =
  | { kind: "group"; id: string; label: string; items: WorkItem[] }
  | { kind: "item"; item: WorkItem }
  | { kind: "findings"; item: WorkItem }
  | { kind: "finding"; text: string }
  | { kind: "down" };

export class Board implements vscode.TreeDataProvider<Node> {
  private changed = new vscode.EventEmitter<Node | void>();
  readonly onDidChangeTreeData = this.changed.event;
  showAll = false;

  constructor(private store: Store, private repos: () => string[]) {
    store.onChange(() => this.changed.fire());
    store.onConnection(() => this.changed.fire());
  }

  refresh = () => this.changed.fire();

  private unlocated(item: WorkItem) {
    const d = this.store.detail(item.id);
    return d ? findingsOf(d).unlocated : [];
  }

  getChildren(node?: Node): Node[] {
    if (node?.kind === "item") return this.unlocated(node.item).length ? [{ kind: "findings", item: node.item }] : [];
    if (node?.kind === "findings") return this.unlocated(node.item).map((f) => ({ kind: "finding", text: `${f.severity}: ${f.message}` }));
    if (node?.kind === "group") return node.items.map((item) => ({ kind: "item", item }));
    if (node) return [];
    if (!this.store.connected && this.store.items().length === 0) return [{ kind: "down" }];
    return groupItems(this.store.items(), this.showAll ? "all" : this.repos()).map((g) => ({ kind: "group", ...g }));
  }

  getTreeItem(node: Node): vscode.TreeItem {
    if (node.kind === "down") {
      const t = new vscode.TreeItem("Kraft isn't running — Start");
      t.iconPath = new vscode.ThemeIcon("debug-disconnect");
      t.command = { command: "kraft.startDaemon", title: "Start Kraft" };
      return t;
    }
    if (node.kind === "group") {
      const t = new vscode.TreeItem(`${node.label} (${node.items.length})`, vscode.TreeItemCollapsibleState.Expanded);
      t.contextValue = "group";
      return t;
    }
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
    t.contextValue = contextValueOf(item);
    t.tooltip = tooltipOf(item);
    return t;
  }

  badge(): vscode.ViewBadge | undefined {
    const n = needsYouCount(this.store.items());
    return n ? { value: n, tooltip: `${n} waiting on you` } : undefined;
  }
}

export function registerBoard(context: vscode.ExtensionContext, store: Store, api: Api, runDir: () => string, refusal: () => string | null) {
  const repos = () => (vscode.workspace.workspaceFolders ?? []).map((f) => f.uri.fsPath);
  const board = new Board(store, repos);
  const view = vscode.window.createTreeView("kraft.board", { treeDataProvider: board });
  const updateBadge = () => (view.badge = board.badge());
  store.onChange(updateBadge);

  const itemOf = (node: { item?: WorkItem } | undefined) => node?.item;
  const act = (action: ItemAction) => async (node?: { item?: WorkItem }) => {
    const item = itemOf(node);
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
  for (const [a, id] of Object.entries(COMMANDS) as [ItemAction, string][]) {
    context.subscriptions.push(vscode.commands.registerCommand(id, mutating(id, refusal, warn, act(a))));
  }
  context.subscriptions.push(
    view,
    vscode.commands.registerCommand("kraft.toggleAllRepos", () => {
      board.showAll = !board.showAll;
      board.refresh();
    }),
    vscode.commands.registerCommand("kraft.openWorktree", (node?: { item?: WorkItem }) => {
      const item = itemOf(node);
      if (item) void vscode.commands.executeCommand("vscode.openFolder", vscode.Uri.file(`${runDir()}/worktrees/${item.id}`), { forceNewWindow: true });
    }),
    vscode.commands.registerCommand("kraft.openInWebUi", (node?: { item?: WorkItem }) => {
      const item = itemOf(node);
      if (item) void vscode.env.openExternal(vscode.Uri.parse(`${api.base}/work-items/${encodeURIComponent(item.id)}`));
    }),
    // Only Restricted Mode refuses: a version mismatch must not stop starting Kraft.
    // From home, so the workspace's shell hooks, venv or shims cannot pick the `kraft` binary.
    vscode.commands.registerCommand("kraft.startDaemon", mutating("kraft.startDaemon", () => (refusal() === RESTRICTED ? RESTRICTED : null), warn, () => {
      const t = vscode.window.createTerminal({ name: "Kraft", cwd: homedir() });
      t.sendText("kraft admin start");
      t.show();
    })),
  );
  return board;
}
