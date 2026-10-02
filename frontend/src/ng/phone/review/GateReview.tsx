import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import { docBody } from "../../../format";
import type { KraftEvent, ReviewThread } from "../../../types";
import { rejectTarget } from "../../item/graph";
import { useEvents } from "../../item/useEvents";
import { useItem, type ItemDetail } from "../../item/useItem";
import { approveBlock } from "../../review/finish";
import { useSubmit } from "../../review/FinishReview";
import { parsePatch, type PatchFile } from "../../review/patch";
import { readReview } from "../../review/url";
import { useArtifact, useCompare, useThreads } from "../../review/useReview";
import { Button } from "../../ui/Button";
import { Markdown } from "../../ui/Markdown";
import { Doc } from "../doc/Doc";
import { ScreenHeader } from "../nav/ScreenHeader";
import { useBack } from "../nav/trail";
import { ActionBar } from "../ui/Rows";
import "./review.css";

/** The agent reviewer's verdict at a gate, from its own event (never invented). */
function autoVerdict(events: KraftEvent[], gate: string) {
  const e = [...events].reverse().find((x) => (x.type === "gate_approved" || x.type === "gate_rejected") && (x.payload.gate ?? x.node_id) === gate && x.payload.by === "agent");
  if (!e) return null;
  const text = String(e.payload.note ?? e.payload.reason ?? e.payload.summary ?? "").trim();
  return { approve: e.type === "gate_approved", text };
}

/** `/work-items/:id/review` (W17 brief F): a gate's document and the files that changed, read-only, with Request changes and Approve. */
export function GateReviewRoute() {
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const loaded = useItem(id);
  const events = useEvents(id, loaded.state === "ready" ? loaded.item.updated_at : "");
  if (params.get("doc")) return <Doc id={params.get("doc")!} />;
  if (loaded.state === "loading")
    return (
      <>
        <ScreenHeader />
        <div className="ph-content ph-skeleton" aria-busy="true"><span /><span /></div>
      </>
    );
  if (loaded.state === "missing")
    return (
      <>
        <ScreenHeader />
        <div className="ph-content"><h1 className="ph-title">Not found</h1><p className="ph-empty">This work item does not exist.</p></div>
      </>
    );
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
  const verdict = gate ? autoVerdict(events, gate) : null;
  const block = approveBlock(item, gate, threads);
  const target = (item.pending_gate === gate ? item.fix_target?.node : null) ?? (gate ? rejectTarget(item.chain_definition.nodes, gate) : null);
  const [openFile, setOpenFile] = useState<string | null>(place.file);
  useEffect(() => setOpenFile(place.file), [place.file]);

  if (params.get("compose") === "reject") return <ReviewComposer target={target} submit={submit} />;

  const sub = hasDoc && artifact?.state === "ready" ? artifact.data.path : [`${files.length} ${files.length === 1 ? "file" : "files"}`, `+${adds} −${dels}`, open ? `${open} open ${open === 1 ? "thread" : "threads"}` : null].filter(Boolean).join(" · ");
  const approve = async () => {
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
      <ScreenHeader id={<span className="ph-mono">{gate ?? ""}</span>} />
      <div className="ph-content">
        <div className="ph-review-head">
          <h1 className="ph-review-title">{hasDoc && artifact?.state === "ready" ? artifact.data.title : item.title}</h1>
          <p className="ph-review-sub">{sub}</p>
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
      {block && <p className="ph-block-reason" role="status">{block}</p>}
      <ActionBar>
        <Button className="ph-btn" onClick={reject} disabled={busy || !gate}>Request changes</Button>
        <Button className="ph-btn ph-btn-primary" variant="primary" onClick={approve} disabled={busy || !!block}>Approve</Button>
      </ActionBar>
    </>
  );
}

/** One thread under its file, read-only: an agent's note or a person's. */
function ThreadNote({ thread }: { thread: ReviewThread }) {
  const first = thread.comments[0];
  const agent = first && first.author !== "you";
  const line = thread.start_line ? `line ${thread.start_line}` : "";
  return (
    <div className={`ph-thread${thread.state === "resolved" ? " ph-is-resolved" : ""}`}>
      <b className="ph-thread-head">{agent ? "agent" : "you"}{line && ` · ${line}`}{thread.label ? ` · ${thread.label.replace("_", " ")}` : ""}{thread.state === "resolved" ? " · resolved" : ""}</b>
      {thread.comments.map((c) => <div key={c.id} className="ph-thread-body"><Markdown text={c.body} /></div>)}
    </div>
  );
}

/** Request changes: a note, the target, one primary (the same review the desktop's Finish review sends). */
function ReviewComposer({ target, submit }: { target: string | null; submit: ReturnType<typeof useSubmit> }) {
  const back = useBack();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const send = async () => {
    setBusy(true);
    setError(null);
    const e = await submit("request_changes", text.trim());
    setBusy(false);
    if (e) setError(e);
  };
  return (
    <>
      <header className="ph-composer-head">
        <button type="button" className="ph-back" onClick={back.go}>Cancel</button>
        <h1 className="ph-composer-title">Request changes</h1>
        <span className="ph-composer-pad" />
      </header>
      <div className="ph-content">
        <p className="ph-help">{`The item goes back to ${target ?? "the previous node"} with your note as the first thing the agent reads.`}</p>
        <textarea className="ph-input ph-input-area" aria-label="Your note" placeholder="What needs to change?" value={text} onChange={(e) => setText(e.target.value)} />
        <p className="ph-target">Reject target: {target ?? "the previous node"}</p>
        {error && <p className="ph-error" role="alert">{error}</p>}
      </div>
      <div className="ph-actionbar">
        <Button className="ph-btn ph-btn-primary" variant="primary" disabled={busy || !text.trim()} onClick={send}>Request changes</Button>
      </div>
    </>
  );
}
