import { readdir, readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import * as vscode from "vscode";
import { ABOUT, configListing } from "../core/scope";

type Pick = vscode.QuickPickItem & { open?: string };

const tilde = (path: string) => (path.startsWith(homedir()) ? `~${path.slice(homedir().length)}` : path);
const separator = (label: string): Pick => ({ label, kind: vscode.QuickPickItemKind.Separator });

/** A chain's own `description:`, when its file has one at the top level. */
async function chainDescription(path: string): Promise<string | undefined> {
  const text = await readFile(path, "utf8").catch(() => "");
  return text.match(/^description:[ \t]*["']?(.+?)["']?[ \t]*$/m)?.[1];
}

export function registerOpenConfig(context: vscode.ExtensionContext, templatesDir: string) {
  context.subscriptions.push(vscode.commands.registerCommand("kraft.openConfig", async () => {
    let names: string[];
    try {
      const chains = await readdir(join(templatesDir, "chains")).catch(() => []);
      names = [...(await readdir(templatesDir)), ...chains.map((c) => `chains/${c}`)];
    } catch {
      const start = "Start Kraft";
      const pick = await vscode.window.showInformationMessage(`No Kraft config at ${templatesDir} yet. Kraft writes it the first time it starts.`, start);
      if (pick === start) await vscode.commands.executeCommand("kraft.startDaemon");
      return;
    }
    const { configuration, templates } = configListing(names);
    const section = (label: string, rows: Pick[]) => (rows.length ? [separator(label), ...rows] : []);
    const items: Pick[] = [
      ...section("Configuration", configuration.map((rel) => ({ label: rel, description: ABOUT[rel], iconPath: new vscode.ThemeIcon("gear"), open: rel }))),
      ...section("Templates", await Promise.all(templates.map(async (rel): Promise<Pick> => rel === "library.yaml"
        ? { label: rel, description: ABOUT[rel], iconPath: new vscode.ThemeIcon("library"), open: rel }
        : { label: rel.slice("chains/".length, -".yaml".length), description: await chainDescription(join(templatesDir, rel)), iconPath: new vscode.ThemeIcon("list-ordered"), open: rel }))),
      separator(""),
      { label: "Open Config Folder in New Window", description: tilde(templatesDir), iconPath: new vscode.ThemeIcon("folder-opened") },
    ];
    const pick = await vscode.window.showQuickPick(items, { title: "Kraft Config", placeHolder: "Open a config file or chain", matchOnDescription: true });
    if (!pick) return;
    if (pick.open) await vscode.window.showTextDocument(vscode.Uri.file(join(templatesDir, pick.open)));
    else await vscode.commands.executeCommand("vscode.openFolder", vscode.Uri.file(templatesDir), { forceNewWindow: true });
  }));
}
