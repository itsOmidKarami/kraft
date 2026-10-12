import * as vscode from "vscode";
import type { Refusal } from "../core/mustFix";

/** Lists the threads that block Approve; picking one goes to it. Resolves once shown, not once picked. */
export async function showRefusal(id: string, r: Refusal, reveal: (id: string, tid: string) => Promise<boolean | undefined>): Promise<void> {
  void vscode.window.showQuickPick(r.items, { title: r.title, placeHolder: "Pick a thread to go to it" }).then(async (pick) => {
    if (pick && (await reveal(id, pick.tid)) === false) void vscode.window.showInformationMessage("That thread is no longer there.");
  });
}
