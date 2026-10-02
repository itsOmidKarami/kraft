import { useEffect, useState } from "react";
import * as api from "../../../api";
import { ago } from "../../../format";
import type { KraftEvent, WorkerSession } from "../../../types";
import { Button } from "../../ui/Button";
import { act } from "../actions";
import { messagesThrough } from "../nodeGraph";
import type { ItemDetail } from "../useItem";
import { sendOnModEnter } from "../../keys";

type Turn = { thread: number; turn: number; who: string; text: string; at: string; node: string | null; session: string | null };

/** The escalation's thread (Decisions §6 Escalation, prototype lines 167–176):
 *  every message through the turn picked above the tabs, by thread and turn,
 *  with the node it was about; a reply goes on in the same thread or starts a
 *  new one (GAP §2 #14). */
export function Thread({ item, version, node, upTo, reload, onNode }: { item: ItemDetail; version: string; node: string; upTo?: WorkerSession; reload: () => void; onNode: (node: string) => void }) {
  const [events, setEvents] = useState<KraftEvent[] | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    // Each read of the item reads the thread again: an older read that answers late must not win.
    let live = true;
    api.getEvents(item.id).then((e) => live && setEvents(e), () => live && setEvents([]));
    return () => { live = false; };
  }, [item.id, version]);
  const all: Turn[] = (events ?? []).filter((e) => e.type === "escalation_message").map((e) => ({
    thread: Number(e.payload.thread ?? 1),
    turn: Number(e.payload.turn ?? 1),
    who: e.payload.auto ? "kraft" : "you",
    text: String(e.payload.message ?? ""),
    at: e.created_at,
    node: e.node_id ?? null,
    session: typeof e.payload.session_id === "string" ? e.payload.session_id : null,
  }));
  const turns = all.slice(0, messagesThrough(all, upTo, item));
  const threads = [...new Set(turns.map((t) => t.thread))];
  const send = async (fresh: boolean) => {
    setBusy(true);
    setError(null);
    const r = await act.escalate(item.id, text.trim(), fresh);
    setBusy(false);
    if (!r.ok) return setError(r.error);
    setText("");
    reload();
  };
  return (
    <>
      {events == null ? <p className="item-muted">Reading the thread…</p> : !turns.length && <p className="item-muted">No messages yet.</p>}
      {threads.map((th) => {
        const these = turns.filter((t) => t.thread === th);
        const total = all.filter((t) => t.thread === th).length;
        return (
          <section key={th} className="ip-thread" aria-label={`Thread ${th}`}>
            <p className="ip-thread-head">thread {th} · {these.length < total ? `turn ${these.length} of ${total}` : `${total} ${total === 1 ? "turn" : "turns"}`}</p>
            {these.map((t, i) => (
              <article key={i} className={`ip-turn${t.node === node ? " is-here" : ""}`}>
                <header className="ip-turn-head">
                  <span className="is-mono">turn {t.turn}{t.node ? ` · ${t.node}` : ""}</span>
                  <span className="item-muted">{ago(t.at)}</span>
                  {t.node && t.node !== node && <button type="button" className="item-link" onClick={() => onNode(t.node!)}>open node →</button>}
                </header>
                <p className="ip-turn-who is-mono">{t.who}</p>
                <p className="ip-turn-text">{t.text}</p>
              </article>
            ))}
          </section>
        );
      })}
      {turns.length < all.length && <p className="item-muted">{all.length - turns.length} later {all.length - turns.length === 1 ? "message" : "messages"} after this turn.</p>}
      <textarea aria-label="Reply to the escalation" className="item-input" rows={2} placeholder="Reply…" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={sendOnModEnter(() => send(false), !busy && !!text.trim())} />
      {error && <p className="item-error" role="alert">{error}</p>}
      <div className="item-actions">
        <Button variant="primary" disabled={busy || !text.trim()} onClick={() => send(false)}>Send</Button>
        <Button disabled={busy || !text.trim()} onClick={() => send(true)}>Send in a new thread</Button>
      </div>
    </>
  );
}
