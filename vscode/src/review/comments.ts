import * as vscode from "vscode";
import { reportError } from "../actions";
import type { Api, ReviewComment, ReviewThread, ThreadLabel } from "../core/api";
import { mutating } from "../core/guard";
import { commandItemId, leftUri, parseReviewUri, rightUri } from "../core/review";
import type { Store } from "../core/store";
import { reviewGone, type ThreadCache } from "../core/threadCache";
import {
  authorName, commentContext, commentMarkdown, commentPatch, fitPlace, LABEL_TEXT, otherThreadsText, placeThread, quoteOf,
  selectionLines, sideOfScheme, syncPlan, threadContext, threadLabel, toThreadIn, writeTarget, type Lines, type Place, type Side,
} from "../core/threads";
import type { registerDiff } from "./diff";

const OTHER = "kraft-threads";
const lineSource = (doc: vscode.TextDocument) => ({ lineCount: doc.lineCount, lineAt: (n: number) => doc.lineAt(n).text });
const otherUri = (id: string) => vscode.Uri.parse(`${OTHER}:/${id}/Other threads.md`);

/** One drawn comment, and the server comment it shows. */
interface Shown extends vscode.Comment {
  thread: ReviewThread;
  comment: ReviewComment;
}
interface Drawn { key: string; vt: vscode.CommentThread; thread: ReviewThread }

const LABEL_PICKS: { label: string; value: ThreadLabel | null }[] = [
  { label: "No label", value: null },
  ...(Object.entries(LABEL_TEXT) as [ThreadLabel, string][]).map(([value, label]) => ({ label, value })),
];

export function registerComments(
  context: vscode.ExtensionContext,
  store: Store,
  api: Api,
  refusal: () => string | null,
  diff: ReturnType<typeof registerDiff>,
  cache: ThreadCache,
) {
  const controller = vscode.comments.createCommentController("kraft.review", "Kraft review");
  controller.commentingRangeProvider = {
    provideCommentingRanges: (doc) =>
      sideOfScheme(doc.uri.scheme) && refusal() === null ? [new vscode.Range(0, 0, Math.max(0, doc.lineCount - 1), 0)] : [],
  };

  const drawn = new Map<string, Map<string, Drawn>>(); // item → thread id → what is drawn
  const owner = new WeakMap<vscode.CommentThread, { id: string; tid: string }>();
  const others = new Map<string, string>(); // item → the Other threads document's text
  const othersChanged = new vscode.EventEmitter<vscode.Uri>();
  const noticed = new Set<string>(); // items told, since their review last opened, of threads off its files

  const drawnOf = (id: string) => {
    let m = drawn.get(id);
    if (!m) drawn.set(id, (m = new Map()));
    return m;
  };

  const uriOf = (id: string, p: Extract<Place, { kind: "file" }>, baseRef: string) =>
    vscode.Uri.parse(p.side === "new" ? rightUri(id, p.file) : leftUri(id, p.file, baseRef));

  const fill = (vt: vscode.CommentThread, t: ReviewThread) => {
    // A comment being edited keeps its editor: the next load after the save redraws it.
    if (vt.comments.some((c) => c.mode === vscode.CommentMode.Editing)) return;
    vt.comments = t.comments.map((c): Shown => ({
      thread: t,
      comment: c,
      body: new vscode.MarkdownString(commentMarkdown(c)),
      mode: vscode.CommentMode.Preview,
      author: { name: authorName(c) },
      contextValue: commentContext(t, c),
    }));
    vt.label = threadLabel(t);
    vt.contextValue = threadContext(t);
    vt.canReply = true;
    if (!t.draft) vt.state = t.state === "resolved" ? vscode.CommentThreadState.Resolved : vscode.CommentThreadState.Unresolved;
  };

  const adopt = (id: string, vt: vscode.CommentThread, t: ReviewThread, key: string) => {
    owner.set(vt, { id, tid: t.id });
    drawnOf(id).set(t.id, { key, vt, thread: t });
    fill(vt, t);
  };

  const draw = async (id: string) => {
    const opened = diff.opened.get(id);
    const threads = cache.get(id);
    if (!opened || !threads) return;
    const places = new Map<string, Place>();
    const ranges = new Map<string, { uri: vscode.Uri; range: vscode.Range }>();
    for (const t of threads) {
      let p = placeThread(t, opened);
      if (p.kind === "file") {
        const uri = uriOf(id, p, opened.baseRef);
        try {
          // Loaded before it is measured: never clamp against a document VS Code has not read.
          const doc = await vscode.workspace.openTextDocument(uri);
          p = fitPlace(t, p, lineSource(doc), opened.dirty);
          if (p.kind === "file") ranges.set(t.id, { uri, range: new vscode.Range(p.lines.start, 0, p.lines.end, doc.lineAt(p.lines.end).text.length) });
        } catch {
          p = { kind: "elsewhere" };
        }
      }
      places.set(t.id, p);
    }
    // Forgotten or reloaded while documents opened: the newer state draws itself.
    if (cache.get(id) !== threads) return;
    const other = otherThreadsText(id, threads, places);
    others.set(id, other.text);
    othersChanged.fire(otherUri(id));
    for (const [tid, line] of other.lineOf) ranges.set(tid, { uri: otherUri(id), range: new vscode.Range(line, 0, line, 0) });

    // A thread is redrawn only when it moves to another document; on the same one its range moves,
    // so an edit or a half-typed reply survives a shift in lines.
    const keyOf = (t: ReviewThread) => ranges.get(t.id)!.uri.toString();
    const mine = drawnOf(id);
    const plan = syncPlan(new Map([...mine].map(([tid, d]) => [tid, d.key])), threads, keyOf);
    for (const tid of plan.remove) {
      mine.get(tid)?.vt.dispose();
      mine.delete(tid);
    }
    for (const t of plan.update) {
      const d = mine.get(t.id)!;
      const at = ranges.get(t.id)!.range;
      if (!d.vt.range.isEqual(at)) d.vt.range = at;
      mine.set(t.id, { ...d, thread: t });
      fill(d.vt, t);
    }
    for (const t of plan.create) {
      const at = ranges.get(t.id)!;
      adopt(id, controller.createCommentThread(at.uri, at.range, []), t, keyOf(t));
    }

    const off = threads.filter((t) => places.get(t.id)?.kind !== "file").length;
    if (off && !noticed.has(id)) {
      noticed.add(id);
      const msg = `${off} review thread${off === 1 ? "" : "s"} on ${id} ${off === 1 ? "is" : "are"} not on a file in this review.`;
      void vscode.window.showInformationMessage(msg, "Show").then((pick) => { if (pick === "Show") void showOthers(id); });
    }
  };

  // One draw at a time per item: each reads the cache as it is when it starts.
  const drawing = new Map<string, Promise<void>>();
  const queue = (id: string, run: () => unknown) => {
    const next = (drawing.get(id) ?? Promise.resolve()).then(run).then(() => {}, () => {});
    drawing.set(id, next);
    return next;
  };
  const redraw = (id: string) => queue(id, () => draw(id));

  const showOthers = async (id: string) => {
    if (!cache.get(id)) await cache.load(id).catch(() => {});
    await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(otherUri(id)), { preview: false });
  };

  /** Drops everything kept for an item: its cache entry, its drawn threads and its Other threads text. */
  const forgetItem = (id: string) => {
    cache.forget(id);
    for (const d of drawn.get(id)?.values() ?? []) d.vt.dispose();
    drawn.delete(id);
    others.delete(id);
    noticed.delete(id);
    drawing.delete(id);
  };
  const sweep = (ids: string[] | "all") => {
    const seen = ids === "all" ? new Set([...cache.loaded(), ...drawn.keys()]) : ids;
    for (const id of seen) if (reviewGone(store.item(id))) forgetItem(id);
  };

  /** Reload an item's threads; draws follow from the cache's change. */
  const reload = (id: string) => cache.load(id).then(() => redraw(id));

  context.subscriptions.push(
    controller,
    othersChanged,
    vscode.workspace.registerTextDocumentContentProvider(OTHER, {
      onDidChange: othersChanged.event,
      provideTextDocumentContent: (uri) => others.get(parseReviewUri(uri.path).id) ?? "",
    }),
    { dispose: diff.onOpened((id) => { noticed.delete(id); void reload(id).catch(() => {}); }) },
    { dispose: cache.onChange((id) => void redraw(id)) },
    { dispose: store.onEvent((ev) => cache.onEvent(ev)) },
    { dispose: store.onChange(sweep) },
    vscode.window.onDidChangeWindowState((s) => s.focused && cache.loaded().forEach((id) => cache.poke(id))),
  );

  const warn = (msg: string) => void vscode.window.showWarningMessage(msg);
  const pickLabel = async (current?: ThreadLabel | null) => {
    const items = LABEL_PICKS.map((p) => ({ ...p, description: current !== undefined && p.value === current ? "current" : undefined }));
    return (await vscode.window.showQuickPick(items, { placeHolder: "Label" }))?.value;
  };

  /** A new thread on a review document, written to Kraft. */
  const createThread = async (id: string, file: string, side: Side, lines: Lines, body: string, label: ThreadLabel | null) => {
    const scheme = side === "old" ? "kraft-git" : "kraft-wt";
    const opened = diff.opened.get(id);
    const doc = await vscode.workspace.openTextDocument(
      vscode.Uri.parse(side === "new" ? rightUri(id, file) : leftUri(id, file, opened?.baseRef ?? "HEAD")));
    const t = toThreadIn(scheme, file, lines, body, label, opened?.renames, quoteOf(lineSource(doc), lines));
    const thread = await api.createThread(id, t!);
    await reload(id);
    return thread;
  };

  const bodyText = (c: vscode.Comment) => (typeof c.body === "string" ? c.body : c.body.value);
  /** Write, then reload; a refusal or error writes nothing more. */
  const write = async (id: string, run: () => Promise<unknown>) => {
    try {
      await run();
    } catch (e) {
      await reportError(e, store, id);
    }
    await reload(id).catch(() => {});
  };

  const addComment = mutating("kraft.review.addComment", refusal, warn, async (reply: vscode.CommentReply) => {
    const known = owner.get(reply.thread);
    if (known) return write(known.id, () => api.addComment(known.tid, reply.text));
    const { id, file } = parseReviewUri(reply.thread.uri.path);
    const side = sideOfScheme(reply.thread.uri.scheme);
    if (!side) return;
    const pick = await pickLabel();
    if (pick === undefined) {
      if (!reply.thread.comments.length) reply.thread.dispose();
      return void vscode.window.showInformationMessage("Comment not added.");
    }
    const lines = selectionLines(reply.thread.range.start, reply.thread.range.end);
    try {
      const doc = await vscode.workspace.openTextDocument(reply.thread.uri);
      const quote = quoteOf(lineSource(doc), lines);
      const t = await api.createThread(id, toThreadIn(reply.thread.uri.scheme, file, lines, reply.text, pick, diff.opened.get(id)?.renames, quote)!);
      // The thread VS Code drew for the reply becomes this one, unless a draw already drew it; the reload moves it if need be.
      await queue(id, () => (drawnOf(id).has(t.id) ? reply.thread.dispose() : adopt(id, reply.thread, t, reply.thread.uri.toString())));
    } catch (e) {
      await reportError(e, store, id);
      return;
    }
    await reload(id).catch(() => {});
  });

  const shownOf = (c: vscode.Comment) => c as Shown;
  const editComment = mutating("kraft.review.editComment", refusal, warn, (c: vscode.Comment) => {
    const s = shownOf(c);
    const vt = drawnOf(s.thread.work_item_id).get(s.thread.id)?.vt;
    if (!vt) return;
    // Edit the raw body, not its rendering with a suggestion or claim.
    vt.comments = vt.comments.map((x) => (shownOf(x).comment.id === s.comment.id ? { ...x, body: s.comment.body, mode: vscode.CommentMode.Editing } : x));
  });
  const saveComment = mutating("kraft.review.saveComment", refusal, warn, async (c: vscode.Comment) => {
    const s = shownOf(c);
    const body = bodyText(c);
    const w = writeTarget(s.thread, s.comment);
    const vt = drawnOf(s.thread.work_item_id).get(s.thread.id)?.vt;
    if (vt) vt.comments = vt.comments.map((x) => (shownOf(x).comment.id === s.comment.id ? { ...x, mode: vscode.CommentMode.Preview } : x));
    await write(s.thread.work_item_id, () =>
      w.kind === "thread" ? api.patchThread(w.tid, { body }) : api.patchComment(w.cid, commentPatch(s.comment, body)));
    if (vt) fill(vt, drawnOf(s.thread.work_item_id).get(s.thread.id)?.thread ?? s.thread);
  });
  const cancelEdit = (c: vscode.Comment) => {
    const s = shownOf(c);
    const d = drawnOf(s.thread.work_item_id).get(s.thread.id);
    if (!d) return;
    d.vt.comments = d.vt.comments.map((x) => (shownOf(x).comment.id === s.comment.id ? { ...x, mode: vscode.CommentMode.Preview } : x));
    fill(d.vt, d.thread);
  };
  const deleteComment = mutating("kraft.review.deleteComment", refusal, warn, async (c: vscode.Comment) => {
    const s = shownOf(c);
    const w = writeTarget(s.thread, s.comment);
    if (w.kind === "thread") {
      const ok = await vscode.window.showWarningMessage("Delete this draft thread?", { modal: true }, "Delete");
      if (ok !== "Delete") return;
    }
    await write(s.thread.work_item_id, () => (w.kind === "thread" ? api.deleteThread(w.tid) : api.deleteComment(w.cid)));
  });

  const threadOf = (vt: vscode.CommentThread) => {
    const o = owner.get(vt);
    return o && { ...o, thread: drawnOf(o.id).get(o.tid)?.thread };
  };
  const setLabel = mutating("kraft.review.setLabel", refusal, warn, async (vt: vscode.CommentThread) => {
    const o = threadOf(vt);
    if (!o) return;
    const label = await pickLabel(o.thread?.label ?? null);
    if (label === undefined) return;
    await write(o.id, () => api.patchThread(o.tid, { label }));
  });
  const resolveThread = mutating("kraft.review.resolveThread", refusal, warn, async (vt: vscode.CommentThread) => {
    const o = threadOf(vt);
    if (o) await write(o.id, () => api.resolveThread(o.tid));
  });
  const reopenThread = mutating("kraft.review.reopenThread", refusal, warn, async (vt: vscode.CommentThread) => {
    const o = threadOf(vt);
    if (o) await write(o.id, () => api.reopenThread(o.tid));
  });

  const idOf = (arg: unknown) => commandItemId(arg, vscode.window.activeTextEditor?.document.uri, ["kraft-wt", "kraft-git", OTHER]);
  context.subscriptions.push(
    vscode.commands.registerCommand("kraft.review.addComment", addComment),
    vscode.commands.registerCommand("kraft.review.editComment", editComment),
    vscode.commands.registerCommand("kraft.review.saveComment", saveComment),
    vscode.commands.registerCommand("kraft.review.cancelEdit", cancelEdit),
    vscode.commands.registerCommand("kraft.review.deleteComment", deleteComment),
    vscode.commands.registerCommand("kraft.review.setLabel", setLabel),
    vscode.commands.registerCommand("kraft.review.resolveThread", resolveThread),
    vscode.commands.registerCommand("kraft.review.reopenThread", reopenThread),
    vscode.commands.registerCommand("kraft.review.otherThreads", (arg?: unknown) => {
      const id = idOf(arg);
      if (id) return showOthers(id);
    }),
  );

  /** Shows a thread, opening its item's review first if need be; false when it is gone after a reload. */
  const reveal = async (id: string, tid: string): Promise<boolean> => {
    if (!diff.opened.has(id)) await vscode.commands.executeCommand("kraft.reviewChanges", id);
    if (!diff.opened.has(id)) return false; // the review did not open; it has said why
    await reload(id).catch(() => {});
    const d = drawn.get(id)?.get(tid);
    if (!d) return false;
    await vscode.window.showTextDocument(d.vt.uri, { selection: d.vt.range, preview: false });
    d.vt.collapsibleState = vscode.CommentThreadCollapsibleState.Expanded;
    return true;
  };

  return {
    drawn: (id: string) => [...(drawn.get(id)?.keys() ?? [])],
    reload,
    reveal,
    createThread,
  };
}
