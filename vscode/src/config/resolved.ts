import * as vscode from "vscode";
import { ApiError, type Api } from "../core/api";
import { configFile } from "../core/scope";

export function registerResolved(context: vscode.ExtensionContext, api: Api, templatesDir: string) {
  const shown = new Map<string, string>();
  const isChain = (doc: vscode.TextDocument) => configFile(doc.uri.fsPath, templatesDir)?.startsWith("chains/") === true;

  // The unsaved buffer (or a chain the daemon never loaded) is parsed and resolved on the fly.
  const fromText = async (text: string) => {
    const parsed = await api.parse(text);
    if (parsed.error) return { issues: [parsed.error] };
    const r = await api.resolve(parsed.chain);
    return r.issues.length ? { issues: r.issues.map((i) => i.message) } : r.chains[0];
  };

  context.subscriptions.push(
    vscode.workspace.registerTextDocumentContentProvider("kraft-resolved", { provideTextDocumentContent: (uri) => shown.get(uri.path) ?? "" }),
    vscode.languages.registerCodeLensProvider({ language: "yaml" }, {
      provideCodeLenses: (doc) =>
        isChain(doc) ? [new vscode.CodeLens(new vscode.Range(0, 0, 0, 0), { title: "Show resolved chain", command: "kraft.showResolved", arguments: [doc.uri] })] : [],
    }),
    vscode.commands.registerCommand("kraft.showResolved", async (uri?: vscode.Uri) => {
      const doc = uri ? await vscode.workspace.openTextDocument(uri) : vscode.window.activeTextEditor?.document;
      if (!doc || !isChain(doc)) return;
      const id = doc.uri.path.split("/").pop()!.replace(/\.yaml$/, "");
      let body: unknown;
      try {
        if (doc.isDirty) body = await fromText(doc.getText());
        else {
          try {
            body = await api.resolved(id);
          } catch (e) {
            if (!(e instanceof ApiError && e.status === 404)) throw e;
            body = await fromText(doc.getText());
          }
        }
      } catch (e) {
        body = { issues: [String((e as Error).message)] };
      }
      const path = `/${id}.resolved.json`;
      shown.set(path, JSON.stringify(body, null, 2));
      const out = await vscode.workspace.openTextDocument(vscode.Uri.parse(`kraft-resolved:${path}`));
      await vscode.languages.setTextDocumentLanguage(out, "json");
      await vscode.window.showTextDocument(out, { preview: true, viewColumn: vscode.ViewColumn.Beside });
    }),
  );
}
