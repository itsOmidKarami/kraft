import * as vscode from "vscode";
import { findingsByUri, marksBySeverity, showsFindings, type LocatedFinding, type Severity } from "../core/findings";
import type { Store } from "../core/store";

const LEVEL: Record<Severity, vscode.DiagnosticSeverity> = {
  error: vscode.DiagnosticSeverity.Error,
  warning: vscode.DiagnosticSeverity.Warning,
  information: vscode.DiagnosticSeverity.Information,
};

const COLOR: Record<Severity, string> = {
  error: "editorError.foreground",
  warning: "editorWarning.foreground",
  information: "editorInfo.foreground",
};

export function registerFindings(context: vscode.ExtensionContext, store: Store) {
  const collection = vscode.languages.createDiagnosticCollection("kraft-findings");
  const owned = new Map<string, string[]>();
  const byUri = new Map<string, LocatedFinding[]>();
  // A left bar, an overview-ruler tick and the message after the line, in the severity's colour:
  // an Information squiggle does not render on the diff's added lines at all (Kraft-tugdf.20).
  const styles = Object.fromEntries(
    (Object.keys(COLOR) as Severity[]).map((s) => {
      const color = new vscode.ThemeColor(COLOR[s]);
      return [
        s,
        vscode.window.createTextEditorDecorationType({
          isWholeLine: true,
          borderWidth: "0 0 0 3px",
          borderStyle: "solid",
          borderColor: color,
          overviewRulerColor: color,
          overviewRulerLane: vscode.OverviewRulerLane.Right,
          after: { color, margin: "0 0 0 2em" },
        }),
      ];
    }),
  ) as Record<Severity, vscode.TextEditorDecorationType>;

  const decorate = (editor: vscode.TextEditor) => {
    const marks = marksBySeverity(byUri.get(editor.document.uri.toString()));
    for (const s of Object.keys(styles) as Severity[]) {
      editor.setDecorations(
        styles[s],
        marks[s].map((f) => ({
          range: new vscode.Range(f.line, 0, f.line, 0),
          hoverMessage: f.message,
          renderOptions: { after: { contentText: f.message } },
        })),
      );
    }
  };

  const apply = (id: string) => {
    for (const key of owned.get(id) ?? []) {
      collection.delete(vscode.Uri.parse(key));
      byUri.delete(key);
    }
    owned.delete(id);
    const detail = store.detail(id);
    if (detail && showsFindings(detail)) {
      const keys: string[] = [];
      for (const [raw, findings] of findingsByUri(id, detail)) {
        const uri = vscode.Uri.parse(raw);
        collection.set(
          uri,
          findings.map((f) => {
            const d = new vscode.Diagnostic(new vscode.Range(f.line, 0, f.line, Number.MAX_SAFE_INTEGER), f.message, LEVEL[f.severity]);
            d.source = f.source;
            return d;
          }),
        );
        byUri.set(uri.toString(), findings);
        keys.push(uri.toString());
      }
      owned.set(id, keys);
    }
    vscode.window.visibleTextEditors.forEach(decorate);
  };

  store.onChange((ids) => (ids === "all" ? [...owned.keys()] : ids).forEach(apply));
  context.subscriptions.push(
    collection,
    ...Object.values(styles),
    vscode.window.onDidChangeVisibleTextEditors((editors) => editors.forEach(decorate)),
  );
}
