import { useEffect, useState } from "react";
import { usd } from "../../../format";
import type { CancelPreview } from "../../../types";
import { act, cancelPreview, type Done } from "../../item/actions";
import { rejectTarget } from "../../item/graph";
import { actionPath } from "../../item/paths";
import type { ItemDetail } from "../../item/useItem";
import { Button } from "../../ui/Button";
import { showToast } from "../../ui/Toast";
import { useBack } from "../nav/trail";
import { SwitchRow } from "../ui/Rows";
import { Facts } from "../ui/Rows";

export type ComposeKind = "steer" | "reject" | "answer" | "escalate" | "cancel" | "complete";
export const COMPOSE_KINDS: ComposeKind[] = ["steer", "reject", "answer", "escalate", "cancel", "complete"];
export const isComposeKind = (v: string | null): v is ComposeKind => COMPOSE_KINDS.includes(v as ComposeKind);

const running = (item: ItemDetail) => item.display_status === "running";
/** A stop no resume takes (a cap, a stuck loop): `/steer` and `/resume` answer it 409, and `/retry` carries the note instead. */
const stopped = (item: ItemDetail) => item.display_status === "failed" || (item.display_status === "needs_you" && item.stop?.kind !== "question");
const gateOf = (item: ItemDetail) => item.pending_gate ?? item.stop?.node ?? "";
const nodeOf = (item: ItemDetail) => item.stop?.node ?? item.current_node_id;

/** Steer on a running item pauses it, then resumes it with the note, one call
 *  after the other and never together (R12, R66): the pause is what lets the
 *  agent read the note at its next launch. A pause that went through and a
 *  resume that was refused leaves the item paused, and says so. */
/** The door a steer goes through: a running item's pause then resume, a stopped one's retry, else resume (a pause, a question). */
export const steerDoor = (item: ItemDetail): "pause" | "retry" | "resume" => (stopped(item) ? "retry" : running(item) ? "pause" : "resume");

export async function steer(item: ItemDetail, text: string): Promise<Done & { paused?: boolean }> {
  const door = steerDoor(item);
  if (door === "retry") {
    const node = item.chain_definition.nodes.find((n) => n.id === nodeOf(item));
    return act.retry(item.id, { ...(node && { path: actionPath(node, item.stop?.task) }), steer: text });
  }
  if (door === "pause") {
    const p = await act.pause(item.id);
    if (!p.ok) return p;
    const r = await act.resume(item.id, text);
    return r.ok ? r : { ...r, paused: true };
  }
  return act.resume(item.id, text);
}

interface Spec {
  title: string;
  help: (item: ItemDetail) => string;
  placeholder: string;
  label: string;
  danger?: boolean;
  target?: (item: ItemDetail) => string | null;
}

const SPEC: Record<ComposeKind, Spec> = {
  steer: {
    title: "Steer",
    help: (i) => (running(i) ? `Steering pauses the item. The running attempt${nodeOf(i) ? ` on ${nodeOf(i)}` : ""} stops now and restarts with your note.`
      : stopped(i) ? `Steering retries${nodeOf(i) ? ` ${nodeOf(i)}` : " the item"} with your note as the first thing its next agent reads.`
      : "Your note goes to the next agent launch, and the item resumes with it."),
    placeholder: "What should the next attempt do differently?",
    label: "Send steer",
    target: (i) => (nodeOf(i) ? `goes to ${nodeOf(i)} · added to that agent's next launch` : null),
  },
  reject: {
    title: "Reject",
    help: (i) => `The item goes back to ${rejectTarget(i.chain_definition.nodes, gateOf(i)) ?? "the previous node"} with your note as the first thing the agent reads.`,
    placeholder: "What needs to change?",
    label: "Reject with note",
    target: (i) => `Reject target: ${rejectTarget(i.chain_definition.nodes, gateOf(i)) ?? "the previous node"}`,
  },
  answer: {
    title: "Answer",
    help: (i) => (i.needs_context_question ? `Asked${nodeOf(i) ? ` on ${nodeOf(i)}` : ""}: “${i.needs_context_question}”` : "The agent is waiting for your answer."),
    placeholder: "Write your answer…",
    label: "Send and resume",
  },
  escalate: {
    title: "Escalate",
    help: () => "The escalation agent reads the whole item: every node, round, finding and earlier turn. It either decides and resumes, or comes back to you with a question. The run keeps going meanwhile.",
    placeholder: "What should it look at? e.g. “the review keeps flagging the same race; decide if it's real”",
    label: "Escalate",
  },
  cancel: {
    title: "Cancel this item?",
    help: () => "The run stops where it is. Everything it produced is kept, and a cancelled item stays cancelled.",
    placeholder: "Why? It goes in the run log.",
    label: "Cancel item",
    danger: true,
  },
  complete: {
    title: "Mark complete",
    help: () => "For work that landed somewhere else, or no longer needs the chain. It stops whatever is running and skips the remaining nodes.",
    placeholder: "Why? It goes in the run log.",
    label: "Mark complete",
  },
};

/** A full-screen composer (W17 brief C.7): help, a 16px textarea, a target line, one primary, disabled until it has what it needs. */
export function Composer({ item, kind, reload }: { item: ItemDetail; kind: ComposeKind; reload: () => void }) {
  const back = useBack();
  const spec = SPEC[kind];
  const [text, setText] = useState("");
  const [flag, setFlag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<CancelPreview | null>(null);
  useEffect(() => {
    if (kind !== "cancel") return;
    let live = true;
    void cancelPreview(item.id).then((r) => live && r.ok && setPreview(r.body));
    return () => void (live = false);
  }, [kind, item.id]);

  const flagLabel =
    kind === "escalate" && (item.escalation_threads?.length ?? 0) > 0 ? "Start a new thread"
    : kind === "cancel" && preview?.mr?.state === "open" ? `Also close !${preview.mr.ref} on the forge`
    : kind === "complete" ? "Also close its beads"
    : null;
  const target = spec.target?.(item) ?? null;

  const send = async () => {
    const t = text.trim();
    if (!t) return;
    setBusy(true);
    setError(null);
    let r: Done & { paused?: boolean };
    let ok = "";
    switch (kind) {
      case "steer": r = await steer(item, t); ok = running(item) ? `Steered. ${nodeOf(item) ?? "The item"} is running again.` : stopped(item) ? "Retrying with your steer." : "Steer sent. The item is running again."; break;
      case "reject": r = await act.reject(item.id, gateOf(item), t); ok = "Rejected."; break;
      case "answer": r = await act.resume(item.id, t); ok = "Sent. The item is running again."; break;
      case "escalate": r = await act.escalate(item.id, t, flag); ok = "Escalated. A new thread started."; break;
      case "cancel": r = await act.cancel(item.id, t, flag); ok = "Item cancelled."; break;
      default: r = await act.complete(item.id, t, flag); ok = "Marked complete.";
    }
    setBusy(false);
    if (r.ok) {
      showToast(ok);
      reload();
      return back.go();
    }
    if (r.paused) {
      // The item is paused now; the composer has nothing left to send.
      showToast(`Paused, but the steer was not sent: ${r.error}`);
      reload();
      return back.go();
    }
    setError(r.error);
  };

  return (
    <>
      <header className="ph-composer-head">
        <button type="button" className="ph-back" onClick={back.go}>Cancel</button>
        <h1 className="ph-composer-title">{spec.title}</h1>
        <span className="ph-composer-pad" />
      </header>
      <div className="ph-content">
        <p className="ph-help">{spec.help(item)}</p>
        <textarea className="ph-input ph-input-area" aria-label={kind === "cancel" || kind === "complete" ? "Reason" : "Your note"} placeholder={spec.placeholder} value={text} onChange={(e) => setText(e.target.value)} />
        {flagLabel && <div className="ph-list"><SwitchRow label={flagLabel} on={flag} onChange={setFlag} /></div>}
        {kind === "cancel" && preview && (
          <Facts rows={[
            ["keeps", `branch ${preview.kept.branch}, the worktree until it is archived${preview.kept.findings ? `, ${preview.kept.findings} findings` : ""}${preview.kept.threads ? `, ${preview.kept.threads} threads` : ""} and the run log`],
            ["spend", `${usd(preview.spend.spent_usd)} stays on the ledger`],
          ]} />
        )}
        {target && <p className="ph-target">{target}</p>}
        {error && <p className="ph-error" role="alert">{error}</p>}
      </div>
      <div className="ph-actionbar">
        <Button className="ph-btn ph-btn-primary" variant={spec.danger ? "danger" : "primary"} disabled={busy || !text.trim()} onClick={send}>{spec.label}</Button>
      </div>
    </>
  );
}
