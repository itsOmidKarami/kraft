import { hostname } from "node:os";
import * as vscode from "vscode";
import { RESTRICTED } from "./core/readOnly";
import { clientName, signIn, type SessionStore } from "./core/session";
import { hostLabel } from "./core/serverUrl";
import { runSignIn } from "./core/signInFlow";

/** `kraft.signIn`: ask for the server's address and password, keep the session
 *  in secret storage, point `kraft.url` at it and reload, since the extension
 *  reads its connection once at activation. Restricted Mode refuses it: it
 *  writes a credential and the URL. */
export function registerSignIn(context: vscode.ExtensionContext, sessions: SessionStore, refusal: () => string | null) {
  context.subscriptions.push(vscode.commands.registerCommand("kraft.signIn", async () => {
    if (refusal() === RESTRICTED) return void vscode.window.showWarningMessage(RESTRICTED);
    const name = clientName(hostname());
    const outcome = await runSignIn({
      prefill: vscode.workspace.getConfiguration("kraft").get<string>("url") ?? "",
      askUrl: (prefill) => Promise.resolve(vscode.window.showInputBox({
        title: "Sign in to a Kraft server",
        prompt: "The server's address, such as https://my-server:8765",
        value: prefill,
        ignoreFocusOut: true,
      })),
      askPassword: () => Promise.resolve(vscode.window.showInputBox({
        title: "Sign in to a Kraft server",
        prompt: "The server's password",
        password: true,
        ignoreFocusOut: true,
      })),
      confirmPlainHttp: async (host) => {
        const go = "Sign in anyway";
        const pick = await vscode.window.showWarningMessage(
          `The password will cross the network unencrypted: http:// to ${host}. Sign in anyway?`, { modal: true }, go);
        return pick === go;
      },
      signIn: (base, password) => Promise.resolve(vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Signing in…" },
        () => signIn(base, password, name))),
      store: sessions,
      writeUrl: (url) => Promise.resolve(vscode.workspace.getConfiguration("kraft").update("url", url, vscode.ConfigurationTarget.Global)),
    });
    if (outcome.kind === "refused") return void vscode.window.showErrorMessage(outcome.message);
    if (outcome.kind === "cancelled") return;
    void vscode.window.showInformationMessage(`Signed in to ${hostLabel(outcome.url)}. Reloading the window…`);
    await vscode.commands.executeCommand("workbench.action.reloadWindow");
  }));
}
