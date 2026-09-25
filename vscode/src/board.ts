import * as vscode from "vscode";
import { deriveState } from "../../frontend/src/deriveState";
import { runAction } from "./actions";
import type { Api, WorkItem } from "./core/api";
import { actionsFor, describe, groupItems, needsYouCount, type Action } from "./core/board";
import type { Store } from "./core/store";

type Node = { kind: "group"; id: string; label: string; items: WorkItem[] } | { kind: "item"; item: WorkItem } | { kind: "down" };

const ICONS: Record<string, string> = {
  gate: "pass", paused: "debug-pause", not_started: "circle-outline", running: "sync~spin",
  rate_limited: "watch", waiting: "watch", escalating: "comment-discussion", escalated: "comment",
  capped: "error", question: "question", budget: "credit-card", done: "check", abandoned: "circle-slash",
};

export class Board implements vscode.TreeDataProvider<Node> {
  private changed = new vscode.EventEmitter<Node | void>();
  readonly onDidChangeTreeData = this.changed.event;
  showAll = false;

  constructor(private store: Store, private repos: () => string[]) {
    store.onChange(() => this.changed.fire());
    store.onConnection(() => this.changed.fire());
  }

  refresh = () => this.changed.fire();

  getChildren(node?: Node): Node[] {
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
    const { item } = node;
    const t = new vscode.TreeItem(item.title);
    t.id = item.id;
    t.description = describe(item);
    t.iconPath = new vscode.ThemeIcon(ICONS[deriveState(item).state] ?? "circle-filled");
    t.contextValue = `item:${[...(item.pending_gate ? ["gate"] : []), ...actionsFor(item)].join(",")}`;
    t.tooltip = `${item.id} — ${item.status}`;
    return t;
  }

  badge(): vscode.ViewBadge | undefined {
    const n = needsYouCount(this.store.items());
    return n ? { value: n, tooltip: `${n} waiting on you` } : undefined;
  }
}

export function registerBoard(context: vscode.ExtensionContext, store: Store, api: Api, runDir: () => string, readOnly: () => boolean) {
  const repos = () => (vscode.workspace.workspaceFolders ?? []).map((f) => f.uri.fsPath);
  const board = new Board(store, repos);
  const view = vscode.window.createTreeView("kraft.board", { treeDataProvider: board });
  const updateBadge = () => (view.badge = board.badge());
  store.onChange(updateBadge);

  const itemOf = (node: { item?: WorkItem } | undefined) => node?.item;
  const act = (action: Action) => async (node?: { item?: WorkItem }) => {
    const item = itemOf(node);
    if (!item) return;
    if (readOnly()) return void vscode.window.showWarningMessage("Kraft is read-only: the daemon's version does not match this extension.");
    await runAction(api, store, item, action);
  };
  for (const a of ["pause", "resume", "retry", "cancel", "skip", "escalate", "archive"] as Action[]) {
    context.subscriptions.push(vscode.commands.registerCommand(`kraft.${a}`, act(a)));
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
    vscode.commands.registerCommand("kraft.startDaemon", () => {
      const t = vscode.window.createTerminal("Kraft");
      t.sendText("kraft admin start");
      t.show();
    }),
  );
  return board;
}
