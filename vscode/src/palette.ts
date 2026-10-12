import * as vscode from "vscode";
import type { WorkItem } from "./core/api";
import { gatePicks, itemPick, pickableFor, PICK_TITLE, type PickVerb } from "./core/picks";
import { clickTarget, PRIMARY_COMMAND } from "./core/primary";

/** Asks which of `items` a row command run from the palette acts on, among
 *  those `verb` applies to now; says so instead when none does. */
export async function pickItem(verb: PickVerb, items: WorkItem[]): Promise<WorkItem | undefined> {
  const title = PICK_TITLE[verb];
  return pickFrom(pickableFor(verb, items), `Pick an item to ${title}`, `Nothing here can ${title} now.`);
}

async function pickFrom(items: WorkItem[], placeHolder: string, none: string): Promise<WorkItem | undefined> {
  if (!items.length) return void vscode.window.showInformationMessage(none);
  const pick = await vscode.window.showQuickPick(items.map(itemPick), { placeHolder, matchOnDescription: true });
  return pick && items.find((i) => i.id === pick.id);
}

/** Approve Next Gate… and Open Item…: palette entries over the window's
 *  items. Both reads, so unguarded; an approve still goes through the gate
 *  document, which must be read first. */
export function registerPalette(
  context: vscode.ExtensionContext,
  inScope: () => WorkItem[],
  reveal: (id: string) => Promise<boolean>,
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("kraft.approveNextGate", async () => {
      const item = await pickFrom(gatePicks(inScope()), "Pick a gate to review", "Nothing here is waiting at a gate.");
      if (item) await vscode.commands.executeCommand(PRIMARY_COMMAND.openArtifact, { item });
    }),
    vscode.commands.registerCommand("kraft.openItem", async () => {
      const item = await pickFrom(inScope(), "Pick an item to open", "Nothing here to open.");
      if (!item) return;
      await reveal(item.id);
      await vscode.commands.executeCommand(PRIMARY_COMMAND[clickTarget(item)], { item });
    }),
  );
}
