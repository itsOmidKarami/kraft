import * as vscode from "vscode";
import type { Api } from "../core/api";
import { fromCheck, fromLint, mergeLint, type Diag } from "../core/configDiagnostics";
import { configFile, isLibraryFile } from "../core/scope";

const SOURCE = "Kraft · config";

function toDiagnostic(d: Diag): vscode.Diagnostic {
  const diag = new vscode.Diagnostic(new vscode.Range(d.line, d.column, d.line, Number.MAX_SAFE_INTEGER), d.message, vscode.DiagnosticSeverity.Error);
  diag.source = SOURCE;
  if (d.related) {
    diag.relatedInformation = [
      new vscode.DiagnosticRelatedInformation(
        new vscode.Location(vscode.Uri.file(d.related.file), new vscode.Position(d.related.line - 1, d.related.column - 1)),
        "inherited from here",
      ),
    ];
  }
  return diag;
}

export function registerConfigChecks(context: vscode.ExtensionContext, api: Api, templatesDir: string, readOnly: () => boolean) {
  const collection = vscode.languages.createDiagnosticCollection("kraft-config");
  const timers = new Map<string, ReturnType<typeof setTimeout>>();
  const clean = new Map<string, boolean>(); // latest check result per file
  const seq = new Map<string, number>(); // newest check per file wins
  let linted = new Set<string>();
  const state: { lastOffer?: "reload" | "restart"; diagnostics(uri: vscode.Uri): readonly vscode.Diagnostic[] } = {
    diagnostics: (uri) => collection.get(uri) ?? [],
  };

  const check = async (doc: vscode.TextDocument) => {
    const rel = configFile(doc.uri.fsPath, templatesDir);
    if (!rel) return;
    const mine = (seq.get(doc.uri.fsPath) ?? 0) + 1;
    seq.set(doc.uri.fsPath, mine);
    try {
      const { issues } = await api.check(rel, doc.getText());
      if (seq.get(doc.uri.fsPath) !== mine) return;
      collection.set(doc.uri, fromCheck(issues, doc.uri.fsPath).map(toDiagnostic));
      clean.set(doc.uri.fsPath, issues.length === 0);
    } catch {
      if (seq.get(doc.uri.fsPath) === mine) clean.delete(doc.uri.fsPath); // daemon down: no verdict
    }
  };

  const schedule = (doc: vscode.TextDocument) => {
    if (!configFile(doc.uri.fsPath, templatesDir)) return;
    clearTimeout(timers.get(doc.uri.fsPath));
    timers.set(doc.uri.fsPath, setTimeout(() => void check(doc), 500));
  };

  const lint = async (): Promise<boolean | undefined> => {
    try {
      const report = await api.lint();
      const dirty = new Set(vscode.workspace.textDocuments.filter((d) => d.isDirty).map((d) => d.uri.fsPath));
      const { set, clear } = mergeLint(linted, fromLint(report.issues), dirty);
      for (const f of clear) collection.delete(vscode.Uri.file(f));
      for (const [f, diags] of set) collection.set(vscode.Uri.file(f), diags.map(toDiagnostic));
      linted = new Set(set.keys());
      return report.valid;
    } catch {
      return undefined;
    }
  };

  const offer = async (doc: vscode.TextDocument) => {
    const rel = configFile(doc.uri.fsPath, templatesDir);
    if (!rel) return;
    await check(doc);
    const libraryValid = isLibraryFile(rel) ? await lint() : true;
    if (readOnly() || clean.get(doc.uri.fsPath) !== true || libraryValid !== true) return;
    if (isLibraryFile(rel) || rel === "policy.yaml") {
      state.lastOffer = "reload";
      if ((await vscode.window.showInformationMessage(`Saved ${rel}. Load it into the running Kraft?`, "Reload Kraft")) !== "Reload Kraft") return;
      try {
        const r = await api.reload();
        const problems = [...Object.entries(r.invalid_templates).map(([k, v]) => `${k}: ${v}`), ...(r.refused_policy ? [`policy.yaml: ${r.refused_policy}`] : [])];
        if (problems.length) void vscode.window.showWarningMessage(`Kraft reloaded with problems: ${problems.join("; ")}`);
        else void vscode.window.showInformationMessage("Kraft reloaded.");
      } catch (e) {
        void vscode.window.showErrorMessage(String((e as Error).message));
      }
    } else {
      state.lastOffer = "restart";
      if ((await vscode.window.showInformationMessage(`Saved ${rel}. Kraft reads it at startup.`, "Restart Kraft")) !== "Restart Kraft") return;
      const t = vscode.window.createTerminal("Kraft");
      t.sendText("kraft admin restart");
      t.show();
    }
  };

  context.subscriptions.push(
    collection,
    vscode.workspace.onDidOpenTextDocument((doc) => void check(doc)),
    vscode.workspace.onDidChangeTextDocument((e) => schedule(e.document)),
    vscode.workspace.onDidSaveTextDocument((doc) => void offer(doc)),
    vscode.commands.registerCommand("kraft.lintLibrary", () => void lint()),
  );
  vscode.workspace.textDocuments.forEach((doc) => void check(doc));
  void lint();
  return state;
}
