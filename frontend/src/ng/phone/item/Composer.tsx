import { useReason } from "../../ui/Reason";
import { ADD_NOTE, WRITE_FIRST } from "../../ui/reasons";
import { REQUEST_CHANGES, sendsBack } from "../../review/finish";
import { nameOf } from "../../board/rowText";
import { useEffect, useState } from "react";
import { usd } from "../../../format";
import type { CancelPreview } from "../../../types";
import { act, cancelPreview, type Done } from "../../item/actions";
import { rejectTarget } from "../../item/graph";
import { actionPath } from "../../item/paths";
import type { ItemDetail } from "../../item/useItem";
import { Button } from "../../ui/Button";
import { showToast } from "../../ui/Toast";
import { ComposerHeader } from "../nav/ScreenHeader";
import { useBack } from "../nav/trail";
import { ActionBar, Facts, SwitchRow } from "../ui/Rows";
import { running, steerDoor, stopped } from "./steer";

export type ComposeKind = "steer" | "reject" | "answer" | "escalate" | "cancel" | "complete";
export const COMPOSE_KINDS: ComposeKind[] = ["steer", "reject", "answer", "escalate", "cancel", "complete"];
export const isComposeKind = (v: string | null): v is ComposeKind => COMPOSE_KINDS.includes(v as ComposeKind);

const gateOf = (item: ItemDetail) => item.pending_gate ?? item.stop?.node ?? "";
const nodeOf = (item: ItemDetail) => item.stop?.node ?? item.current_node_id;
/** The node as a reader names it. */
const rejectName = (i: ItemDetail) => { const id = rejectTarget(i.chain_definition.nodes, gateOf(i)); return id ? nameOf(i.chain_definition.nodes, id) : "the previous node"; };
const nodeName = (item: ItemDetail) => { const id = nodeOf(item); return id ? nameOf(item.chain_definition.nodes, id) : null; };

/** Sends the note through the door `steerDoor` names. A stopped item's retry
 *  carries it, at the node it stopped on. A running item is paused, then
 *  resumed with it, one call after the other and never together (R12, R66):
 *  the pause is what lets the agent read the note at its next launch, and a
 *  pause that went through with its resume refused leaves the item paused, and
 *  says so. Anything else resumes with it. */
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
  label: string | ((item: ItemDetail) => string);
  danger?: boolean;
  target?: (item: ItemDetail) => string | null;
}

const SPEC: Record<ComposeKind, Spec> = {
  steer: {
    title: "Steer",
    help: (i) => (running(i) ? `Steering pauses the item. The running attempt${nodeOf(i) ? ` on ${nodeName(i)}` : ""} stops now and restarts with your note.`
      : stopped(i) ? `Steering retries${nodeOf(i) ? ` ${nodeName(i)}` : " the item"} with your note as the first thing its next agent reads.`
      : "Your note goes to the next agent launch, and the item resumes with it."),
    placeholder: "What should the next attempt do differently?",
    label: "Send steer",
    target: (i) => (nodeOf(i) ? `goes to ${nodeName(i)} · added to that agent's next launch` : null),
  },
  reject: {
    title: REQUEST_CHANGES,
    help: (i) => `${sendsBack(false)}${rejectName(i)}, with your note as the first thing the agent reads.`,
    placeholder: "What needs to change?",
    label: REQUEST_CHANGES,
  },
  answer: {
    title: "Answer",
    help: (i) => (i.needs_context_question ? `Asked${nodeOf(i) ? ` on ${nodeName(i)}` : ""}: “${i.needs_context_question}”` : "The agent is waiting for your answer."),
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

/** A full-screen composer (W17 brief C.7): help, a 16px textarea, a target line, one primary that says what is missing when pressed without it. */
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

  const [tried, setTried] = useState(false);
  // Said after a press with nothing written, and gone once there is something.
  const noText = useReason(tried && !text.trim() ? (kind === "reject" ? ADD_NOTE : WRITE_FIRST) : null);
  const send = async () => {
    const t = text.trim();
    if (!t) return setTried(true);
    setBusy(true);
    setError(null);
    let r: Done & { paused?: boolean };
    let ok = "";
    switch (kind) {
      case "steer": r = await steer(item, t); ok = running(item) ? `Steered. ${nodeName(item) ?? "The item"} is running again.` : stopped(item) ? "Retrying with your steer." : "Steer sent. The item is running again."; break;
      case "reject": r = await act.reject(item.id, gateOf(item), t); ok = "Changes requested."; break;
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
      showToast(`Paused, but the steer was not sent: ${r.error}`, { tone: "error" });
      reload();
      return back.go();
    }
    setError(r.error);
  };

  return (
    <>
      <ComposerHeader title={spec.title} onCancel={back.go} />
      <div className="ph-content">
        <p className="ph-help">{spec.help(item)}</p>
        {/* Focus starts in the box, as in the desktop's dialogs: it opened on <body>, so a keyboard or switch user had to find it first (R12b-13). */}
        <textarea autoFocus className="ph-input ph-input-area" aria-label={kind === "cancel" || kind === "complete" ? "Reason" : "Your note"} {...noText.attrs} placeholder={spec.placeholder} value={text} onChange={(e) => setText(e.target.value)} />
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
      <ActionBar>
        {noText.note}
        <Button className="ph-btn ph-btn-primary" variant={spec.danger ? "danger" : "primary"} disabled={busy} onClick={send}>{typeof spec.label === "function" ? spec.label(item) : spec.label}</Button>
      </ActionBar>
    </>
  );
}
