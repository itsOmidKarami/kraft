import { useState, type ReactNode } from "react";
import type { ReviewComment, ReviewThread, Suggestion, ThreadLabel } from "../../types";
import { detailOf, jsonBody, request } from "../http";
import { Button } from "../ui/Button";
import { Markdown } from "../ui/Markdown";
import { languageOf, tokenizeSide } from "./tokenize";
import { sendOnModEnter } from "../keys";

/** Fenced code in a comment, coloured by the review's own tokenizer. */
export function codeBlock(text: string, lang: string | undefined): ReactNode {
  return tokenizeSide(text.split("\n"), languageOf(`x.${lang ?? ""}`)).map((line, i) => (
    <span key={i}>
      {i > 0 && "\n"}
      {line.map((t, j) => <span key={j} className={t.cls ? `tok-${t.cls}` : undefined}>{t.text}</span>)}
    </span>
  ));
}
const Body = ({ text }: { text: string }) => <Markdown text={text} code={codeBlock} />;

export const LABELS: [ThreadLabel | null, string][] = [[null, "No label"], ["must_fix", "Must fix"], ["question", "Question"], ["nit", "Nit"]];
const TAG: Record<ThreadLabel, string> = { must_fix: "MUST FIX", question: "QUESTION", nit: "NIT" };
const CLAIM: Record<NonNullable<ReviewComment["claim"]>, string> = { fixed: "✓ claimed fixed", answered: "✓ answered", should_fix: "should fix" };
const STATUS = (t: ReviewThread) => (t.draft ? "pending" : t.state);

/** "Line 5", "Lines 5–7", "Old line 4": where a line comment sits, as its composer and its thread name it. */
export const rangeName = (r: { side: "old" | "new"; start: number; end: number }) =>
  `${r.side === "old" ? "Old line" : "Line"}${r.start === r.end ? ` ${r.start}` : `s ${r.start}–${r.end}`}`;

/** What a refused write said, or null when it landed. */
type Act = () => Promise<string | null>;
const send = async (path: string, init: RequestInit): Promise<string | null> => {
  const { status, body } = await request(path, init);
  return status >= 200 && status < 300 ? null : detailOf(body);
};

/** The replaced lines of a suggestion: `−` the lines it anchors to, `+` the replacement. */
function SuggestionBlock({ s, old }: { s: Suggestion; old: string[] }) {
  return (
    <div className="rv-suggest">
      <div className="rv-suggest-head">Suggested change</div>
      {old.map((l, i) => <div key={`o${i}`} className="rv-suggest-line is-del"><span aria-hidden="true">−</span>{l}</div>)}
      {s.replacement.split("\n").map((l, i) => <div key={`n${i}`} className="rv-suggest-line is-add"><span aria-hidden="true">+</span>{l}</div>)}
    </div>
  );
}

/** One review thread in the diff (prototype 433–466). */
export function Thread({ thread, oldLines, onChanged, onEdit }: {
  thread: ReviewThread;
  /** The new-side text of lines `a..b`, for a suggestion's `−` rows. */
  oldLines: (a: number, b: number) => string[];
  onChanged: () => void;
  /** A draft thread's Edit: reopen the composer on it. */
  onEdit: (t: ReviewThread) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [replying, setReplying] = useState(false);
  const [reply, setReply] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (act: Act) => {
    setBusy(true);
    const e = await act();
    setBusy(false);
    setError(e);
    if (!e) onChanged();
    return e;
  };
  const saveEdit = async (id: string) => !(await run(() => send(`/comments/${id}`, jsonBody("PATCH", { body: editText.trim() })))) && setEditing(null);
  const addReply = async () => {
    if (!(await run(() => send(`/threads/${thread.id}/comments`, jsonBody("POST", { body: reply.trim() }))))) { setReplying(false); setReply(""); }
  };
  const [first, ...rest] = thread.comments;
  const who = (c: ReviewComment) => (c.author === "you" ? "You" : c.author);
  return (
    <article className={`rv-thread${thread.state === "resolved" ? " is-resolved" : ""}`} aria-label={`Thread on ${thread.file_path ?? "the item"}`}>
      <div className="rv-thread-head">
        <span className="rv-who">{first ? who(first) : "You"}</span>
        {thread.label && <span className={`rv-tag is-${thread.label}`}>{TAG[thread.label]}</span>}
        <span className="rv-status">{STATUS(thread)}</span>
        {thread.start_line !== null && thread.side && <span className="rv-status">· {rangeName({ side: thread.side, start: thread.start_line, end: thread.end_line ?? thread.start_line })}</span>}
      </div>
      {first && <Body text={first.body} />}
      {first?.suggestion && <SuggestionBlock s={first.suggestion} old={oldLines(first.suggestion.start_line, first.suggestion.end_line)} />}
      {thread.draft && (
        <div className="rv-links">
          <button type="button" className="rv-link" onClick={() => onEdit(thread)}>Edit</button>
          <span aria-hidden="true">·</span>
          <button type="button" className="rv-link" onClick={() => run(() => send(`/threads/${thread.id}`, { method: "DELETE" }))}>Delete</button>
        </div>
      )}
      {rest.map((c) => (
        <div key={c.id} className={`rv-reply${c.author === "you" ? "" : " is-agent"}`}>
          <div className="rv-thread-head">
            <span className="rv-who rv-mono">{who(c)}</span>
            {c.attempt !== null && c.author !== "you" && <span className="rv-muted">attempt {c.attempt}</span>}
            {c.claim && <span className={`rv-claim is-${c.claim}`}>{CLAIM[c.claim]}</span>}
            {c.draft && <span className="rv-status">pending</span>}
          </div>
          {editing === c.id ? (
            <div className="rv-reply-edit">
              <textarea className="rv-textarea" aria-label="Edit reply" value={editText} onChange={(e) => setEditText(e.target.value)} onKeyDown={sendOnModEnter(() => saveEdit(c.id), !busy && !!editText.trim())} />
              <div className="rv-row-actions">
                <Button onClick={() => setEditing(null)}>Cancel</Button>
                <Button variant="primary" disabled={busy || !editText.trim()} onClick={() => saveEdit(c.id)}>Save</Button>
              </div>
            </div>
          ) : (
            <Body text={c.body} />
          )}
          {c.suggestion && <SuggestionBlock s={c.suggestion} old={oldLines(c.suggestion.start_line, c.suggestion.end_line)} />}
          {c.draft && editing !== c.id && (
            <div className="rv-links">
              <button type="button" className="rv-link" onClick={() => { setEditing(c.id); setEditText(c.body); }}>Edit</button>
              <span aria-hidden="true">·</span>
              <button type="button" className="rv-link" onClick={() => run(() => send(`/comments/${c.id}`, { method: "DELETE" }))}>Delete</button>
            </div>
          )}
        </div>
      ))}
      {!thread.draft && replying && (
        <div className="rv-reply-edit">
          <textarea className="rv-textarea" aria-label="Reply" placeholder="Reply…" value={reply} onChange={(e) => setReply(e.target.value)} onKeyDown={sendOnModEnter(addReply, !busy && !!reply.trim())} autoFocus />
          <div className="rv-row-actions">
            <span className="rv-muted">Sent with your next review</span>
            <span className="rv-spacer" />
            <Button onClick={() => { setReplying(false); setReply(""); }}>Cancel</Button>
            <Button variant="primary" disabled={busy || !reply.trim()} onClick={addReply}>Add reply</Button>
          </div>
        </div>
      )}
      {!thread.draft && !replying && (
        <div className="rv-row-actions">
          <span className="rv-spacer" />
          {thread.state !== "resolved" && <Button onClick={() => setReplying(true)}>Reply</Button>}
          {thread.state === "resolved"
            ? <Button onClick={() => run(() => send(`/threads/${thread.id}/reopen`, { method: "POST" }))}>Reopen</Button>
            : <Button variant="primary" onClick={() => run(() => send(`/threads/${thread.id}/resolve`, { method: "POST" }))}>Resolve</Button>}
        </div>
      )}
      {error && <p className="rv-error" role="alert">{error}</p>}
    </article>
  );
}
