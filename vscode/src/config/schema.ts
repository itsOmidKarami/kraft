import { readFileSync } from "node:fs";
import { join } from "node:path";
import * as vscode from "vscode";
import { configFile, schemaFor } from "../core/scope";

interface YamlApi {
  registerContributor(
    schema: string,
    requestSchema: (resource: string) => string | undefined,
    requestSchemaContent: (uri: string) => string,
  ): boolean;
}

const PROMPTED = "kraft.yamlPrompted";

export async function registerSchemas(context: vscode.ExtensionContext, templatesDir: string): Promise<void> {
  const ext = vscode.extensions.getExtension<YamlApi>("redhat.vscode-yaml");
  if (!ext) {
    if (!context.globalState.get(PROMPTED)) {
      await context.globalState.update(PROMPTED, true);
      const pick = await vscode.window.showInformationMessage(
        "Install Red Hat YAML for completion and instant structure checks in Kraft config files.",
        "Install",
      );
      if (pick === "Install") await vscode.commands.executeCommand("workbench.extensions.installExtension", "redhat.vscode-yaml");
    }
    return;
  }
  const yaml = await ext.activate();
  const dir = join(context.extensionPath, "schemas");
  yaml.registerContributor(
    "kraft",
    (resource) => {
      const rel = configFile(vscode.Uri.parse(resource).fsPath, templatesDir);
      return rel ? `kraft-schema://schemas/${schemaFor(rel)}` : undefined;
    },
    (uri) => readFileSync(join(dir, vscode.Uri.parse(uri).path.replace(/^\//, "")), "utf8"),
  );
}
