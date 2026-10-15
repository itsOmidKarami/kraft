import * as vscode from "vscode";
import { reportError } from "./actions";
import type { Api, WorkItem } from "./core/api";
import { docPath, docText } from "./core/docs";
import { isWebUrl } from "./core/links";
import type { Store } from "./core/store";
import { noMergeRequest } from "./core/wording";
import { pickItem } from "./palette";

const SCHEME = "kraft-doc";
const texts = new Map<string, string>(); // uri path → markdown
let changed: vscode.EventEmitter<vscode.Uri> | undefined; // set by `registerDocs`

/** Shows `markdown` (once `registerDocs` has run) in a read-only tab named after `title`; `key` keeps two
 *  documents with one title apart, and a second open of the same key and
 *  title reloads the tab already showing it. */
export async function openReadOnlyDoc(key: string, title: string, markdown: string, truncated?: boolean): Promise<void> {
  const path = docPath(key, title);
  texts.set(path, docText(markdown, truncated));
  // `Uri.from`, not `Uri.parse`: a `#`, `?` or `%` in a title stays in the path.
  const uri = vscode.Uri.from({ scheme: SCHEME, path });
  changed?.fire(uri); // a tab already open on this path reloads
  const doc = await vscode.workspace.openTextDocument(uri);
  await vscode.window.showTextDocument(doc, { preview: false });
}

/** Open Merge Request, Open Spec and Open Plan on a board row, or on an item
 *  picked from the palette. All reads, so unguarded. */
export function registerDocs(context: vscode.ExtensionContext, store: Store, api: Api, inScope: () => WorkItem[]): void {
  const attachment = (kind: "spec" | "plan") => async (node?: { item?: WorkItem }) => {
    const id = (node?.item ?? (await pickItem(kind === "spec" ? "openSpec" : "openPlan", inScope())))?.id;
    if (!id) return;
    try {
      const a = await api.attachment(id, kind);
      await openReadOnlyDoc(`${id}/${kind}`, a.title, a.content, a.truncated);
    } catch (e) {
      await reportError(e, store, id); // a 404 says the item has none, or Kraft lost its copy
    }
  };
  changed = new vscode.EventEmitter<vscode.Uri>();
  context.subscriptions.push(
    changed,
    { dispose: () => texts.clear() },
    vscode.workspace.registerTextDocumentContentProvider(SCHEME, {
      onDidChange: changed.event,
      provideTextDocumentContent: (uri) => texts.get(uri.path) ?? "",
    }),
    vscode.commands.registerCommand("kraft.openMergeRequest", async (node?: { item?: WorkItem }) => {
      const item = node?.item ?? (await pickItem("openMergeRequest", inScope()));
      if (!item) return;
      const url = (store.item(item.id) ?? item).mr_ref?.url;
      // The row token already asked; the row may be older than the store.
      if (!isWebUrl(url)) return void vscode.window.showInformationMessage(noMergeRequest(item.title));
      await vscode.env.openExternal(vscode.Uri.parse(url, true));
    }),
    vscode.commands.registerCommand("kraft.openSpec", attachment("spec")),
    vscode.commands.registerCommand("kraft.openPlan", attachment("plan")),
  );
}
