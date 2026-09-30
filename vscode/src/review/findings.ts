import * as vscode from "vscode";
import { findingsByUri, showsFindings, type Severity } from "../core/findings";
import type { Store } from "../core/store";

const LEVEL: Record<Severity, vscode.DiagnosticSeverity> = {
  error: vscode.DiagnosticSeverity.Error,
  warning: vscode.DiagnosticSeverity.Warning,
  information: vscode.DiagnosticSeverity.Information,
};

export function registerFindings(context: vscode.ExtensionContext, store: Store) {
  const collection = vscode.languages.createDiagnosticCollection("kraft-findings");
  const owned = new Map<string, vscode.Uri[]>();

  const apply = (id: string) => {
    for (const uri of owned.get(id) ?? []) collection.delete(uri);
    owned.delete(id);
    const detail = store.detail(id);
    if (!detail || !showsFindings(detail)) return;
    const uris: vscode.Uri[] = [];
    for (const [key, findings] of findingsByUri(id, detail)) {
      const uri = vscode.Uri.parse(key);
      collection.set(
        uri,
        findings.map((f) => {
          const d = new vscode.Diagnostic(new vscode.Range(f.line, 0, f.line, Number.MAX_SAFE_INTEGER), f.message, LEVEL[f.severity]);
          d.source = f.source;
          return d;
        }),
      );
      uris.push(uri);
    }
    owned.set(id, uris);
  };

  store.onChange((ids) => (ids === "all" ? [...owned.keys()] : ids).forEach(apply));
  context.subscriptions.push(collection);
}
