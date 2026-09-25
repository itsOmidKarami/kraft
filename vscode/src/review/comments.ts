import * as vscode from "vscode";
import { reportError } from "../actions";
import { ApiError, type Api } from "../core/api";
import { composeNote, destinationOf, type Draft, type DraftComment } from "../core/note";
import { parseReviewUri } from "../core/review";
import type { Store } from "../core/store";

export function registerComments(context: vscode.ExtensionContext, store: Store, api: Api, readOnly: () => boolean) {
  const key = (id: string) => `kraft.draft.${id}`;
  const drafts = (id: string): Draft => context.workspaceState.get<Draft>(key(id)) ?? { comments: [] };
  const save = (id: string, d: Draft) => context.workspaceState.update(key(id), d.comments.length || d.summary ? d : undefined);

  const controller = vscode.comments.createCommentController("kraft.review", "Kraft review");
  controller.commentingRangeProvider = {
    provideCommentingRanges: (doc) =>
      doc.uri.scheme === "kraft-wt" || doc.uri.scheme === "kraft-git" ? [new vscode.Range(0, 0, Math.max(0, doc.lineCount - 1), 0)] : [],
  };

  // ponytail: inline bubbles are not re-drawn after a reload; the draft and
  // Submit Review survive. Upgrade: recreate threads from the draft when a review opens.
  const addDraft = (id: string, c: DraftComment) => {
    const d = drafts(id);
    d.comments.push(c);
    void save(id, d);
  };

  context.subscriptions.push(
    controller,
    vscode.commands.registerCommand("kraft.review.addComment", (reply: vscode.CommentReply) => {
      const { id, file } = parseReviewUri(reply.thread.uri.path);
      const line = (reply.thread.range?.start.line ?? 0) + 1;
      addDraft(id, { file, line, body: reply.text });
      reply.thread.comments = [
        ...reply.thread.comments,
        { body: reply.text, mode: vscode.CommentMode.Preview, author: { name: "You (draft)" } },
      ];
      reply.thread.canReply = true;
    }),
  );

  const clear = (id: string) => save(id, { comments: [] });

  const submit = async (id: string, summary?: string): Promise<boolean> => {
    if (readOnly()) return false;
    const d = drafts(id);
    if (!d.comments.length && !summary?.trim()) {
      void vscode.window.showInformationMessage("No review comments to send.");
      return false;
    }
    await store.refresh(id).catch(() => {});
    const dest = destinationOf(store.detail(id));
    if (dest.kind === "none") {
      void vscode.window.showWarningMessage(dest.reason);
      return false;
    }
    const note = composeNote({ ...d, summary });
    try {
      if (dest.kind === "reject") await api.reject(id, dest.gate, note);
      else await api.resume(id, note);
      await clear(id);
      await store.refresh(id);
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) await clear(id);
      await reportError(e, store, id);
      return false;
    }
  };

  const confirmApprove = async (id: string): Promise<boolean> => {
    const n = drafts(id).comments.length;
    if (!n) return true;
    const pick = await vscode.window.showWarningMessage(`Discard ${n} review comment${n === 1 ? "" : "s"} and approve?`, { modal: true }, "Discard and Approve");
    if (pick !== "Discard and Approve") return false;
    await clear(id);
    return true;
  };

  const idOfEditor = () => {
    const uri = vscode.window.activeTextEditor?.document.uri;
    return uri && (uri.scheme === "kraft-wt" || uri.scheme === "kraft-git" || uri.scheme === "kraft-artifact")
      ? uri.path.split("/")[1]
      : undefined;
  };
  context.subscriptions.push(
    vscode.commands.registerCommand("kraft.review.submit", async (arg?: string | { item?: { id: string } }) => {
      const id = typeof arg === "string" ? arg : (arg?.item?.id ?? idOfEditor());
      if (!id) return;
      const summary = await vscode.window.showInputBox({ prompt: "Overall comment (optional)" });
      if (summary === undefined) return;
      await submit(id, summary);
    }),
    vscode.commands.registerCommand("kraft.review.discard", async (arg?: string | { item?: { id: string } }) => {
      const id = typeof arg === "string" ? arg : (arg?.item?.id ?? idOfEditor());
      if (id) await clear(id);
    }),
  );

  return { drafts, addDraft, submit, confirmApprove };
}
