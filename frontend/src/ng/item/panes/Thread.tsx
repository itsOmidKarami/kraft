import { useEffect, useState } from "react";
import * as api from "../../../api";
import { ago } from "../../../format";
import type { KraftEvent } from "../../../types";
import { Button } from "../../ui/Button";
import { act } from "../actions";
import type { ItemDetail } from "../useItem";
import { sendOnModEnter } from "../../keys";

type Turn = { thread: number; turn: number; who: string; text: string; at: string; node: string | null };

/** The escalation's thread (Decisions §6 Escalation, prototype lines 167–176):
 *  every message, by thread and turn, with the node it was about; a reply goes
 *  on in the same thread or starts a new one (GAP §2 #14). */
export function Thread({ item, node, reload, onNode }: { item: ItemDetail; node: string; reload: () => void; onNode: (node: string) => void }) {
  const [events, setEvents] = useState<KraftEvent[] | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.getEvents(item.id).then(setEvents, () => setEvents([]));
  }, [item.id, item.updated_at]);
  const turns: Turn[] = (events ?? []).filter((e) => e.type === "escalation_message").map((e) => ({
    thread: Number(e.payload.thread ?? 1),
    turn: Number(e.payload.turn ?? 1),
    who: e.payload.auto ? "kraft" : "you",
    text: String(e.payload.message ?? ""),
    at: e.created_at,
    node: e.node_id ?? null,
  }));
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
        return (
          <section key={th} className="ip-thread" aria-label={`Thread ${th}`}>
            <p className="ip-thread-head">thread {th} · {these.length} {these.length === 1 ? "turn" : "turns"}</p>
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
      <textarea aria-label="Reply to the escalation" className="item-input" rows={2} placeholder="Reply…" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={sendOnModEnter(() => send(false), !busy && !!text.trim())} />
      {error && <p className="item-error" role="alert">{error}</p>}
      <div className="item-actions">
        <Button variant="primary" disabled={busy || !text.trim()} onClick={() => send(false)}>Send</Button>
        <Button disabled={busy || !text.trim()} onClick={() => send(true)}>Send in a new thread</Button>
      </div>
    </>
  );
}
