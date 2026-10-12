import * as vscode from "vscode";
import { docTitle } from "../../frontend/src/format";
import { ApiError, type Api } from "./core/api";
import { latest, searchPicks, type SearchPick } from "./core/search";
import { openReadOnlyDoc } from "./docs";

const TITLE = "Kraft: Search";
const DEBOUNCE_MS = 250;
type Row = vscode.QuickPickItem & { pick: SearchPick };

/** `kraft.search`: Kraft's document search in a quick pick. A read, so unguarded.
 *  A hit linked to an item selects it on the board, or opens it in the web UI
 *  when the board does not show it; a hit with no item opens the document. */
export function registerSearch(context: vscode.ExtensionContext, api: Api, reveal: (id: string) => Promise<boolean>): void {
  const open = async (p: SearchPick) => {
    if (p.itemId) {
      if (!(await reveal(p.itemId))) await vscode.commands.executeCommand("kraft.openInWebUi", { item: { id: p.itemId } });
      return;
    }
    try {
      const doc = await api.document(p.docId);
      // Its own key space: an attachment's key is `<item id>/<kind>`. Titled from the
      // whole document, not the snippet the pick's label may have been read from.
      await openReadOnlyDoc(`doc/${p.docId}`, docTitle({ ...doc, ...doc.links[0] }), doc.content);
    } catch (e) {
      void vscode.window.showErrorMessage(e instanceof ApiError ? e.detail : String(e));
    }
  };

  context.subscriptions.push(vscode.commands.registerCommand("kraft.search", () => {
    const qp = vscode.window.createQuickPick<Row>();
    qp.title = TITLE;
    qp.placeholder = "Search Kraft's specs, plans and session summaries";
    const begin = latest();
    let timer: ReturnType<typeof setTimeout> | undefined;
    // The placeholder is hidden once something is typed, so a note or an error goes in the title.
    const say = (msg?: string) => (qp.title = msg ? `${TITLE} — ${msg}` : TITLE);

    qp.onDidChangeValue((value) => {
      clearTimeout(timer);
      const fresh = begin(); // whatever is still in flight is stale now
      const q = value.trim();
      qp.busy = false;
      if (!q) {
        qp.items = [];
        return void say();
      }
      timer = setTimeout(async () => {
        qp.busy = true;
        try {
          const res = await api.search(q);
          if (!fresh()) return;
          // `alwaysShow`: the quick pick's own filter would hide a hit whose words differ from the query's.
          qp.items = searchPicks(res.results).map((pick) => ({ label: pick.label, description: pick.description, detail: pick.detail, alwaysShow: true, pick }));
          say(res.note ?? (res.results.length ? undefined : "nothing found"));
        } catch (e) {
          if (!fresh()) return;
          qp.items = [];
          say(e instanceof ApiError ? e.detail : String(e));
        } finally {
          if (fresh()) qp.busy = false;
        }
      }, DEBOUNCE_MS);
    });
    qp.onDidAccept(() => {
      const row = qp.selectedItems[0];
      if (!row) return;
      qp.hide();
      void open(row.pick);
    });
    qp.onDidHide(() => {
      clearTimeout(timer);
      begin();
      qp.dispose();
    });
    qp.show();
  }));
}
