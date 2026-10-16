import { useEffect, useId, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import { docBody } from "../../../format";
import type { KraftEvent, ReviewThread } from "../../../types";
import { gateMessage, materialized } from "../../item/chainValues";
import { rejectTarget } from "../../item/graph";
import { admits } from "../../item/status";
import { useEvents } from "../../item/useEvents";
import { useItem, type ItemDetail } from "../../item/useItem";
import { useReason } from "../../ui/Reason";
import { ADD_NOTE } from "../../ui/reasons";
import { REQUEST_CHANGES, approveBlock, drafts, gateConsequence, sendsBack } from "../../review/finish";
import { useSubmit } from "../../review/FinishReview";
import { parsePatch, type PatchFile } from "../../review/patch";
import { rangeName, threadRange } from "../../review/range";
import { readReview } from "../../review/url";
import { useArtifact, useCompare, useThreads } from "../../review/useReview";
import { Button } from "../../ui/Button";
import { Markdown } from "../../ui/Markdown";
import { Doc } from "../doc/Doc";
import { ConfirmSheet, useSheet } from "../nav/Sheet";
import { capitalise, gateName, nameOf } from "../../board/rowText";
import { ItemMissing } from "../nav/NotFound";
import { ComposerHeader, ScreenHeader } from "../nav/ScreenHeader";
import { useBack } from "../nav/trail";
import { ActionBar } from "../ui/Rows";
import "./review.css";
import { LINES_COMMENTED_ON, ON_THE_WHOLE_CHANGE } from "../../review/sharedWords";
import { Badge } from "../ui/Badge";

/** The agent reviewer's verdict at a gate, from its own event (never invented). */
function autoVerdict(events: KraftEvent[], gate: string) {
  const e = [...events].reverse().find((x) => (x.type === "gate_approved" || x.type === "gate_rejected") && (x.payload.gate ?? x.node_id) === gate && x.payload.by === "agent");
  if (!e) return null;
  const text = String(e.payload.note ?? e.payload.reason ?? e.payload.summary ?? "").trim();
  return { approve: e.type === "gate_approved", text };
}

/** What became of a gate the item no longer waits at, from its own event. */
function decided(events: KraftEvent[], gate: string | null) {
  const e = gate ? [...events].reverse().find((x) => (x.type === "gate_approved" || x.type === "gate_rejected") && (x.payload.gate ?? x.node_id) === gate) : null;
  if (!e) return "This item is not waiting at this gate.";
  return `${e.type === "gate_approved" ? "Approved" : "Changes requested"}${e.payload.by === "agent" ? " by the agent reviewer" : ""}.`;
}

/** `/work-items/:id/review` (W17 brief F): a gate's document and the files that changed, read-only, with Request changes and Approve. */
export function GateReviewRoute() {
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const loaded = useItem(id);
  const events = useEvents(id, loaded.state === "ready" ? loaded.version : "");
  if (params.get("doc")) return <Doc id={params.get("doc")!} />;
  if (loaded.state === "loading")
    return (
      <>
        <ScreenHeader />
        <div className="ph-content ph-skeleton" aria-busy="true"><span /><span /></div>
      </>
    );
  if (loaded.state === "missing") return <ItemMissing />;
  return <GateReview item={loaded.item} events={events} />;
}

export function GateReview({ item, events }: { item: ItemDetail; events: KraftEvent[] }) {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const place = readReview(params, item);
  const gate = place.gate;
  const compare = useCompare(item.id, place.from, place.to, false, item.head_sha);
  const threadsLoad = useThreads(item.id);
  const threads: ReviewThread[] = threadsLoad.state === "ready" ? threadsLoad.data : [];
  const artifact = useArtifact(item);
  const submit = useSubmit(item, gate, threads, threadsLoad.reload, artifact?.state === "ready" ? artifact.data.digest : null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const hasDoc = !!gate && item.pending_gate === gate && !!item.gate_artifact;
  const files = compare.state === "ready" ? compare.data.files ?? [] : [];
  const patches = useMemo(() => new Map<string, PatchFile>((compare.state === "ready" ? parsePatch(compare.data.diff ?? "") : []).map((f) => [f.path, f])), [compare]);
  const adds = files.reduce((n, f) => n + f.insertions, 0);
  const dels = files.reduce((n, f) => n + f.deletions, 0);
  const open = threads.filter((t) => t.state !== "resolved").length;
  // Your comments not sent yet: Approve would send them as the gate passes, where no agent acts on them (R9b-04 on the phone).
  const pending = drafts(threads).length;
  const sheet = useSheet();
  const verdict = gate ? autoVerdict(events, gate) : null;
  const block = approveBlock(item, gate, threads);
  const blockId = useId();
  const waiting = !!gate && item.pending_gate === gate;
  const next = gateConsequence(item, gate);
  const message = gate ? gateMessage(materialized(item), gate) : undefined;
  // At the waiting gate the server's `fix_target` names where a rejection goes, and its null that there is
  // none (a legacy row, where the reject route answers 409): only a server that sends no `fix_target` gets
  // the chain's guess, as does a gate the item is not waiting at.
  const atGate = item.pending_gate === gate && item.fix_target !== undefined;
  const target = atGate ? item.fix_target?.node ?? null : gate ? rejectTarget(item.chain_definition.nodes, gate) : null;
  const rejectable = admits(item, "reject", true);
  const [openFile, setOpenFile] = useState<string | null>(place.file);
  useEffect(() => setOpenFile(place.file), [place.file]);

  if (params.get("compose") === "reject") return <ReviewComposer target={target && nameOf(item.chain_definition.nodes, target)} submit={submit} />;
  // Comments are written on the desktop; this screen reads the change and decides the gate.
  const readOnly = "Read-only here: add comments on the desktop.";

  // The threads and pending counts show beside the document's path too: every shipped gate has one (R10b-03).
  const counts = [open ? `${open} open ${open === 1 ? "thread" : "threads"}` : null, pending ? `${pending} pending` : null];
  const sub = (hasDoc && artifact?.state === "ready" ? [artifact.data.path, ...counts] : [`${files.length} ${files.length === 1 ? "file" : "files"}`, `+${adds} −${dels}`, ...counts]).filter(Boolean).join(" · ");
  // A thread on no file is on the whole change, listed above the files as the desktop does (R8b-08).
  const whole = threads.filter((t) => !t.file_path);
  const approve = async () => {
    sheet.close();
    setBusy(true);
    setError(null);
    const e = await submit("approve", "");
    setBusy(false);
    if (e) setError(e);
  };
  const reject = () => {
    const next = new URLSearchParams(params);
    next.set("compose", "reject");
    navigate(`?${next}`);
  };

  return (
    <>
      <ScreenHeader id={gate && <>{capitalise(gateName(item.chain_definition.nodes, gate))}{waiting && <> <Badge tone="warn">NEEDS YOU</Badge></>}</>} />
      <div className="ph-content">
        <div className="ph-review-head">
          <h1 className="ph-review-title">{hasDoc && artifact?.state === "ready" ? artifact.data.title : item.title}</h1>
          <p className="ph-review-sub">{sub}</p>
          {message && <p className="ph-review-msg">{message}</p>}
          {next && <p className="ph-review-next">{next}</p>}
          <p className="ph-review-next">{readOnly}</p>
        </div>
        {verdict && (
          <section className="ph-autocard" aria-label="Agent review">
            <span className={`ph-autocard-title ${verdict.approve ? "ph-tone-ok" : "ph-tone-bad"}`}>auto_review · {verdict.approve ? "approve" : "reject"}</span>
            {verdict.text && <span className="ph-autocard-text">{verdict.text}</span>}
          </section>
        )}
        {hasDoc && (
          <section aria-label="Document" className="ph-doc-body">
            {!artifact || artifact.state === "loading" ? <p className="ph-note">Reading the document…</p>
              : artifact.state === "error" ? <p className="ph-error" role="alert">{artifact.error}</p>
              : <>
                  <Markdown text={docBody(artifact.data.content, artifact.data.title)} />
                  {artifact.data.truncated && <p className="ph-note">The document was cut at {Math.round(artifact.data.artifact_max_bytes / 1024)} KB.</p>}
                </>}
          </section>
        )}
        {compare.state === "error" && !hasDoc && <p className="ph-error" role="alert">{compare.error}</p>}
        {whole.length > 0 && (
          <section aria-label={ON_THE_WHOLE_CHANGE} className="ph-list">
            <h2 className="ph-review-group">{ON_THE_WHOLE_CHANGE}</h2>
            {whole.map((t) => <ThreadNote key={t.id} thread={t} />)}
          </section>
        )}
        {files.length > 0 && (
          <section aria-label="Changed files" className="ph-list">
            {files.map((f) => {
              const isOpen = openFile === f.path;
              const mine = threads.filter((t) => t.file_path === f.path);
              const patch = patches.get(f.path);
              return (
                <div key={f.path} className="ph-file">
                  <button type="button" className="ph-row ph-file-row" aria-expanded={isOpen} onClick={() => setOpenFile(isOpen ? null : f.path)}>
                    {isOpen ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
                    <span className="ph-file-name">{f.path}</span>
                    <span className="ph-add">+{f.insertions}</span>
                    <span className="ph-del">−{f.deletions}</span>
                    {mine.some((t) => t.state !== "resolved") && <span className="ph-dot" role="img" aria-label="open thread" />}
                  </button>
                  {isOpen && (
                    <>
                      <div className="ph-diff" role="region" aria-label={`Diff of ${f.path}`} tabIndex={0}>
                        {patch?.binary ? <p className="ph-note">Binary file.</p>
                          : patch ? patch.hunks.flatMap((h) => h.lines.map((l, i) => <div key={`${h.header}${i}`} className={`ph-diff-line${l.kind === "+" ? " ph-diff-add" : l.kind === "-" ? " ph-diff-del" : ""}`}>{l.kind}{l.text}</div>))
                          : <p className="ph-note">The diff is not shown (it was cut for size).</p>}
                      </div>
                      {mine.map((t) => <ThreadNote key={t.id} thread={t} />)}
                    </>
                  )}
                </div>
              );
            })}
          </section>
        )}
        {error && <p className="ph-error" role="alert">{error}</p>}
      </div>
      {waiting ? (
        <>
          {block && <p className="ph-block-reason" id={blockId} role="status">{block}</p>}
          <ActionBar>
            {rejectable && <Button className="ph-btn" onClick={reject} disabled={busy || !gate}>{REQUEST_CHANGES}</Button>}
            <Button className="ph-btn ph-btn-primary" variant="primary" onClick={() => sheet.open("approve")} disabled={busy || !!block} aria-describedby={block ? blockId : undefined}>Approve</Button>
          </ActionBar>
        </>
      ) : (
        <p className="ph-block-reason" role="status">{decided(events, gate)}</p>
      )}
      {sheet.is("approve") && gate && (
        <ConfirmSheet
          title={pending ? `Approve and send ${pending} ${pending === 1 ? "comment" : "comments"}?` : `${capitalise(gateName(item.chain_definition.nodes, gate))}?`}
          text={pending ? `Your pending ${pending === 1 ? "comment goes" : "comments go"} out with the approval, as the gate passes: no agent acts on ${pending === 1 ? "it" : "them"}. To have them acted on, Request changes instead.` : `${next ? `${next}. ` : ""}An approval cannot be taken back.`}
          busy={busy}
          confirm={{ label: "Approve", run: () => void approve() }}
          onClose={sheet.close}
        />
      )}
    </>
  );
}

/** One thread under its file, read-only: an agent's note or a person's. Its
 *  range is named as the desktop names it, side and all, with the lines it
 *  was written on; a comment not sent yet is marked pending (R10b-03). */
function ThreadNote({ thread }: { thread: ReviewThread }) {
  const first = thread.comments[0];
  const agent = first && first.author !== "you";
  const range = threadRange(thread);
  const where = range ? rangeName(range).replace(/^./, (c) => c.toLowerCase()) : "";
  const status = thread.draft ? "pending" : thread.state === "resolved" ? "resolved" : "";
  const quote = range && thread.quote ? thread.quote.split("\n") : [];
  return (
    <div className={`ph-thread${thread.state === "resolved" ? " ph-is-resolved" : ""}`}>
      <b className="ph-thread-head">{agent ? "agent" : "you"}{where && ` · ${where}`}{thread.label ? ` · ${thread.label.replace("_", " ")}` : ""}{status && ` · ${status}`}</b>
      {quote.length > 0 && (
        <div className="ph-thread-quote" role="group" aria-label={LINES_COMMENTED_ON}>
          {quote.map((l, i) => <div key={i} className={`ph-diff-line${l[0] === "+" ? " ph-diff-add" : l[0] === "-" ? " ph-diff-del" : ""}`}>{l}</div>)}
        </div>
      )}
      {thread.comments.map((c) => (
        <div key={c.id} className="ph-thread-body">
          {!thread.draft && c.draft && <span className="ph-thread-pending">pending</span>}
          <Markdown text={c.body} />
        </div>
      ))}
    </div>
  );
}

/** Request changes: a note, the target, one primary (the same review the desktop's Finish review sends). */
function ReviewComposer({ target, submit }: { target: string | null; submit: ReturnType<typeof useSubmit> }) {
  const back = useBack();
  const sheet = useSheet();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tried, setTried] = useState(false);
  // Said after a press with no note, and gone once there is one (the review page does the same).
  const noNote = useReason(tried && !text.trim() ? ADD_NOTE : null);
  const send = async () => {
    if (!text.trim()) return setTried(true);
    setBusy(true);
    setError(null);
    const e = await submit("request_changes", text.trim());
    setBusy(false);
    if (e) setError(e);
  };
  return (
    <>
      <ComposerHeader title={REQUEST_CHANGES} onCancel={() => (text.trim() ? sheet.open("discard") : back.go())} />
      <div className="ph-content">
        <p className="ph-help">{`${sendsBack(false)}${target ?? "the previous node"}, with your note as the first thing the agent reads.`}</p>
        <textarea className="ph-input ph-input-area" aria-label="Your note" {...noNote.attrs} placeholder="What needs to change?" value={text} onChange={(e) => setText(e.target.value)} />
        {error && <p className="ph-error" role="alert">{error}</p>}
      </div>
      {sheet.is("discard") && <ConfirmSheet title="Discard your note?" text="Your note has not been sent." confirm={{ label: "Discard", danger: true, run: back.go }} onClose={sheet.close} />}
      <ActionBar>
        {noNote.note}
        <Button className="ph-btn ph-btn-primary" variant="primary" disabled={busy} onClick={send}>{REQUEST_CHANGES}</Button>
      </ActionBar>
    </>
  );
}
