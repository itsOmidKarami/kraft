import { join } from "node:path";
import * as vscode from "vscode";
import { findingsOf, showsFindings, type Severity } from "../core/findings";
import { rightUri } from "../core/review";
import type { Store } from "../core/store";

const LEVEL: Record<Severity, vscode.DiagnosticSeverity> = {
  error: vscode.DiagnosticSeverity.Error,
  warning: vscode.DiagnosticSeverity.Warning,
  information: vscode.DiagnosticSeverity.Information,
};

export function registerFindings(context: vscode.ExtensionContext, store: Store, worktrees: Map<string, string>, runDir: () => string) {
  const collection = vscode.languages.createDiagnosticCollection("kraft-findings");
  const owned = new Map<string, vscode.Uri[]>();

  const apply = (id: string) => {
    for (const uri of owned.get(id) ?? []) collection.delete(uri);
    owned.delete(id);
    const detail = store.detail(id);
    if (!detail || !showsFindings(detail)) return;
    const root = worktrees.get(id) ?? join(runDir(), "worktrees", id);
    const byUri = new Map<string, { uri: vscode.Uri; diags: vscode.Diagnostic[] }>();
    for (const f of findingsOf(detail).located) {
      const d = new vscode.Diagnostic(new vscode.Range(f.line, 0, f.line, Number.MAX_SAFE_INTEGER), f.message, LEVEL[f.severity]);
      d.source = f.source;
      for (const uri of [vscode.Uri.parse(rightUri(id, f.file)), vscode.Uri.file(join(root, f.file))]) {
        const entry = byUri.get(uri.toString()) ?? { uri, diags: [] };
        entry.diags.push(d);
        byUri.set(uri.toString(), entry);
      }
    }
    for (const { uri, diags } of byUri.values()) collection.set(uri, diags);
    owned.set(id, [...byUri.values()].map((e) => e.uri));
  };

  store.onChange((ids) => (ids === "all" ? [...owned.keys()] : ids).forEach(apply));
  context.subscriptions.push(collection);
}
