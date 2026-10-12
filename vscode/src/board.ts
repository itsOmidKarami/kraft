import { homedir } from "node:os";
import * as vscode from "vscode";
import { runAction } from "./actions";
import type { Api, WorkItem } from "./core/api";
import { actionsFor, describe, groupItems, iconOf, needsYouCount, tooltipOf, type Action, type Include } from "./core/board";
import { findingsOf } from "./core/findings";
import { mutating } from "./core/guard";
import { RESTRICTED } from "./core/readOnly";
import type { WorkspaceScope } from "./core/repoScope";
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

  constructor(private store: Store, private include: Include, scope: Pick<WorkspaceScope, "onChange">) {
    store.onChange(() => this.changed.fire());
    store.onConnection(() => this.changed.fire());
    scope.onChange(() => this.changed.fire());
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
    return groupItems(this.store.items(), this.include).map((g) => ({ kind: "group", ...g }));
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
    t.contextValue = `item:${[...(item.pending_gate ? ["gate"] : []), ...actionsFor(item)].join(",")}`;
    t.tooltip = tooltipOf(item);
    return t;
  }

  badge(): vscode.ViewBadge | undefined {
    const n = needsYouCount(this.store.items(), this.include);
    return n ? { value: n, tooltip: `${n} waiting on you` } : undefined;
  }
}

export function registerBoard(
  context: vscode.ExtensionContext,
  store: Store,
  api: Api,
  runDir: () => string,
  refusal: () => string | null,
  include: Include,
  scope: Pick<WorkspaceScope, "onChange">,
) {
  const board = new Board(store, include, scope);
  const view = vscode.window.createTreeView("kraft.board", { treeDataProvider: board });
  const updateBadge = () => (view.badge = board.badge());
  store.onChange(updateBadge);
  scope.onChange(updateBadge);

  const itemOf = (node: { item?: WorkItem } | undefined) => node?.item;
  const act = (action: Action) => async (node?: { item?: WorkItem }) => {
    const item = itemOf(node);
    if (!item) return;
    await runAction(api, store, item, action);
  };
  const warn = (msg: string) => void vscode.window.showWarningMessage(msg);
  for (const a of ["pause", "resume", "retry", "cancel", "skip", "escalate", "archive"] as Action[]) {
    context.subscriptions.push(vscode.commands.registerCommand(`kraft.${a}`, mutating(`kraft.${a}`, refusal, warn, act(a))));
  }
  context.subscriptions.push(
    view,
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
