import { useNavigate } from "react-router-dom";
import { useEffect, useState, type ReactNode } from "react";
import * as api from "../../api";
import { ago, until, usd } from "../../format";
import type { ChainNode, DiffFile, KraftEvent } from "../../types";
import { CircleHelp, Clock, Pause, X } from "../icons";
import { Button } from "../ui/Button";
import { act } from "./actions";
import { neverStarted, spentLine } from "./status";
import { actionPath, taskName } from "./paths";
import type { ItemDetail } from "./useItem";
import { sendOnModEnter } from "../keys";

type Handlers = {
  reload: () => void;
  /** The header's cancel card and Escalate… dialog. */
  onCancel: () => void;
  onEscalate: () => void;
  onDuplicate: () => void;
  onOpenNode: (node: string) => void;
};
type Card = { tone: "bad" | "info" | "warn" | "muted" | "neutral"; glyph: ReactNode; title: string; where?: string; text?: string; facts: [string, ReactNode][]; actions: { label: string; run: () => unknown; primary?: boolean }[]; node?: string | null; body?: ReactNode };

const str = (v: unknown) => (typeof v === "string" || typeof v === "number" ? String(v) : null);
const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : []);
const pathOf = (task?: string | null) => (task ? task.split(".").join(" › ") : "");

/** One card under the brief for every stop (Decisions §14): glyph and title,
 *  where, a paragraph, up to four facts, actions with the primary first, and
 *  "Open <node> →". Content from `stop` and `stop.facts` only: a fact the
 *  server does not send is left out, never written here. */
export function StateCard({ item, files = null, ...h }: { item: ItemDetail; files?: DiffFile[] | null } & Handlers) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const events = useEndEvents(item);
  const navigate = useNavigate();
  const card = cardFor(item, { ...h, files, onRepos: () => navigate(`/settings/repos/${encodeURIComponent(item.repo)}`), onReview: (nodes) => navigate(`/work-items/${encodeURIComponent(item.id)}/review${nodes ? `?nodes=${encodeURIComponent(nodes)}` : ""}`) }, events, async (p) => {
    setBusy(true);
    setError(null);
    const r = await p;
    setBusy(false);
    if (r.ok) h.reload();
    else setError(r.error);
  });
  if (!card) return null;
  return (
    <section className={`item-card is-${card.tone}`} aria-label={card.title}>
      <h2 className="item-card-head">
        {card.glyph} <span className="item-card-title">{card.title}</span>
        {card.where && <span className="item-card-where">{card.where}</span>}
      </h2>
      {card.text && <p className="item-card-text">{card.text}</p>}
      {card.facts.length > 0 && (
        <dl className="item-facts item-card-facts">
          {card.facts.slice(0, 4).map(([k, v]) => (
            <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
          ))}
        </dl>
      )}
      {card.body}
      {error && <p className="item-error" role="alert">{error}</p>}
      <div className="item-actions">
        {card.actions.map((a) => (
          <Button key={a.label} variant={a.primary ? "primary" : "secondary"} disabled={busy} onClick={() => void a.run()}>{a.label}</Button>
        ))}
        {card.node && <button type="button" className="item-link item-card-open" onClick={() => h.onOpenNode(card.node!)}>Open {card.node} →</button>}
      </div>
    </section>
  );
}

type Run = (p: Promise<{ ok: true } | { ok: false; error: string }>) => Promise<void>;

/** The card's Retry: named by the path it sends, so a stop inside a fix loop
 *  reads "Retry from verification", not "from judge", a task it cannot name. */
function retryFrom(item: ItemDetail, node: ChainNode | undefined, run: Run) {
  const task = item.stop?.task;
  const path = node ? actionPath(node, task) : null;
  const label = task ? `Retry from ${taskName(path ?? task)}` : "Retry";
  return { label, primary: true, run: () => run(act.retry(item.id, path ? { path } : {})) };
}

function cardFor(item: ItemDetail, h: Handlers & { files: DiffFile[] | null; onRepos: () => void; onReview: (nodes?: string) => void }, events: KraftEvent[], run: Run): Card | null {
  const stop = item.stop;
  const facts = (stop?.facts ?? {}) as Record<string, unknown>;
  const node = item.chain_definition.nodes.find((n) => n.id === stop?.node);
  const spent: [string, ReactNode] | null = item.budget_cap ? ["spent", spentLine(item)] : null;
  const where = stop ? [pathOf(stop.task) || stop.node, stop.attempt ? `attempt ${stop.attempt}` : ""].filter(Boolean).join(" · ") : undefined;
  const status = item.display_status;

  if (status === "failed" && stop) {
    const fs: [string, ReactNode][] = Object.entries(facts).flatMap(([k, v]) => (str(v) ? [[k, str(v)!] as [string, ReactNode]] : []));
    // "Do I lose anything?": the branch and what is on it stay.
    const kept = [item.branch && `branch ${item.branch}`, h.files?.length && `${h.files.length} ${h.files.length === 1 ? "file" : "files"}`].filter(Boolean).join(" · ");
    const keptFact: [string, ReactNode][] = kept ? [["work kept", kept]] : [];
    return {
      tone: "bad", glyph: <X size={14} aria-hidden />, title: "Failed", where, text: stop.reason ?? undefined, node: stop.node,
      facts: [...keptFact, ...fs.slice(0, 3 - keptFact.length), ...(spent ? [spent] : [])],
      actions: [
        retryFrom(item, node, run),
        // An infrastructure stop (a token, a forge, a remote) is fixed in the repo's settings. The stop names no
        // cause the label could name ("Fix the token in Repos"), so it says where to look.
        ...(stop.kind === "infra" ? [{ label: "Check the repo settings", run: h.onRepos }] : []),
        { label: "Escalate…", run: h.onEscalate },
      ],
    };
  }
  if (status === "waiting" && stop?.kind === "rate_limit") {
    const harness = str(facts.harness);
    const allowed = list(facts.fallback_allowed);
    const fs: [string, ReactNode][] = [];
    if (stop.resume_at) fs.push(["next retry", until(stop.resume_at)]);
    if (item.rate_limit) fs.push(["retries", `${item.rate_limit.count} of ${item.rate_limit.cap} used (policy: rate-limit retries)`]);
    if (allowed.length) fs.push(["fallback", `${allowed.join(", ")} ${allowed.length === 1 ? "is" : "are"} allowed by the harness list`]);
    return {
      tone: "info", glyph: <Clock size={14} aria-hidden />, title: "Waiting on the provider", where, node: stop.node,
      text: `${harness ?? "The agent"} hit its rate limit. Kraft retries by itself, so nothing needs doing.`,
      facts: fs,
      // No Retry now and no "use another harness" here: /retry claims only a
      // stopped item and answers a waiting one 409 (R10b-01). Pause is the header's.
      actions: [],
    };
  }
  if (status === "waiting" && stop?.kind === "wait")
    return {
      tone: "info", glyph: <Clock size={14} aria-hidden />, title: "Waiting on CI", where, node: stop.node, text: stop.reason ?? undefined,
      facts: stop.resume_at ? [["next check", until(stop.resume_at)]] : [],
      actions: [],
    };
  // B5 (R2): the worker capability is built elsewhere; until the server sends
  // these fields this branch is unreachable and the item shows its plain waiting status.
  if (status === "waiting" && stop && (stop.kind as string) === "worker_lost" && str(facts.last_seen_at) && str(facts.reassign_at)) {
    const online = list(facts.workers_online);
    return {
      tone: "info", glyph: <Clock size={14} aria-hidden />, title: "Worker lost", where: [where, str(facts.worker)].filter(Boolean).join(" · "), node: stop.node,
      text: `The worker running this item stopped answering ${ago(str(facts.last_seen_at))}. The attempt in flight counts as lost. If the worker does not return, Kraft hands the item to another worker.`,
      facts: [
        ["last seen", ago(str(facts.last_seen_at))],
        ["reassigns", `automatically ${until(str(facts.reassign_at))}`],
        ...(online.length ? [["others", `${online.length} ${online.length === 1 ? "worker" : "workers"} online · ${online.join(", ")}`] as [string, ReactNode]] : []),
      ],
      actions: [
        { label: "Reassign now", primary: true, run: () => run(act.reassign(item.id)) },
        { label: "Keep waiting", run: () => run(act.keepWaiting(item.id)) },
      ],
    };
  }
  if (status === "needs_you" && stop?.kind === "conflict") {
    const unresolved = list(facts.unresolved);
    const resolved = list(facts.resolved);
    return {
      tone: "warn", glyph: <CircleHelp size={14} aria-hidden />, title: "Rebase needs you", where, node: stop.node, text: stop.reason ?? undefined,
      facts: [
        ...(unresolved.length ? [["unresolved", unresolved.join(" · ")] as [string, ReactNode]] : []),
        ...(resolved.length ? [["resolved", resolved.join(" · ")] as [string, ReactNode]] : []),
        ...(spent ? [spent] : []),
      ],
      actions: [
        // Decisions §14: the review page on that node.
        { label: "Review the conflicts", primary: true, run: () => h.onReview(stop.node ?? undefined) },
        { label: "Send back with guidance", run: h.onEscalate },
      ],
    };
  }
  if (status === "needs_you" && stop?.kind === "mr_closed") {
    const ref = str(facts.ref) ?? (item.mr_ref ? String(item.mr_ref.number) : "");
    const closed = [...events].reverse().find((e) => e.type === "mr_closed");
    const opened = [...events].reverse().find((e) => e.type === "mr_opened");
    const by = str(closed?.payload.by);
    return {
      tone: "warn", glyph: <CircleHelp size={14} aria-hidden />, title: `MR !${ref} was closed on the forge`,
      where: [stop.node, closed ? `closed ${by ? `by ${by} ` : ""}${ago(closed.created_at)}, not merged` : "not merged"].filter(Boolean).join(" · "),
      node: stop.node,
      text: "Kraft stopped syncing the MR. The branch, the review and all findings are intact. Decide whether this work still goes ahead.",
      facts: [],
      actions: [
        { label: `Reopen !${ref}`, primary: true, run: () => run(act.reopenMr(item.id)) },
        ...(opened?.node_id ? [{ label: "Open a new MR", run: () => run(act.retry(item.id, { path: opened.node_id! })) }] : []),
        { label: "Cancel item…", run: h.onCancel },
      ],
    };
  }
  // Any other stop that needs you: a stuck fix loop, a question with no text, a kind a later server adds.
  // A gate, a question, a cap and a budget have their banner or question card; this one has its own way on (R11a-01).
  if (status === "needs_you" && stop && !(["gate", "budget", "cap"] as string[]).includes(stop.kind) && !(stop.kind === "question" && item.needs_context_question)) {
    return {
      tone: "warn", glyph: <CircleHelp size={14} aria-hidden />, title: stop.kind === "stuck" ? "Stuck" : "Needs you", where, text: stop.reason ?? undefined, node: stop.node,
      facts: spent ? [spent] : [],
      actions: [
        retryFrom(item, node, run),
        { label: "Escalate…", run: h.onEscalate },
      ],
    };
  }
  if (status === "cancelled") {
    const ev = [...events].reverse().find((e) => e.type === "work_item_cancelled");
    const at = str(ev?.payload.node_id) ?? item.current_node_id;
    return {
      tone: "muted", glyph: <Pause size={14} aria-hidden />, title: "Cancelled", where: at ? `at ${at}` : undefined, node: at,
      text: "The run stopped. Everything it produced is kept. A cancelled item stays cancelled; duplicating it starts a new one with the same title, brief and chain.",
      facts: [
        ["kept", "the branch, the worktree until it is archived, findings and threads"],
        ...(spent ? [["spent", usd(item.budget_cap!.spent_usd)] as [string, ReactNode]] : []),
        ["reason", str(ev?.payload.reason) || "no reason given"],
      ],
      actions: [
        { label: "Duplicate as new item", primary: true, run: h.onDuplicate },
        { label: "Archive", run: () => run(act.archive(item.id)) },
      ],
    };
  }
  return null;
}

/** The events a card needs that the detail does not carry (who closed the MR,
 *  which node opened it, the cancel's reason): read once, for those cards only. */
function useEndEvents(item: ItemDetail): KraftEvent[] {
  const wants = item.display_status === "cancelled" || item.stop?.kind === "mr_closed";
  const [events, setEvents] = useState<KraftEvent[]>([]);
  useEffect(() => {
    if (!wants) return setEvents([]);
    api.getEvents(item.id).then(setEvents, () => setEvents([]));
  }, [item.id, wants, item.updated_at]);
  return events;
}

/** The paused card (Decisions §6 Steer and pause, prototype lines 85–90): a
 *  steer, its target when more than one agent task is paused, and Resume. A
 *  never-started item gets the not-started card instead: nothing ran, so
 *  there is nothing to resume or steer; Start is the main button. */
export function PausedCard({ item, reload }: { item: ItemDetail; reload: () => void }) {
  const paused = item.worker_sessions.filter((s) => s.status === "paused" && s.hook_point.split(".").length === 3);
  const [steer, setSteer] = useState(item.pending_steer_context ?? "");
  const [target, setTarget] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (item.display_status !== "paused") return null;
  if (neverStarted(item)) return <NotStartedCard item={item} />;
  // No agent task paused at this node: the server refuses a steer, so offer plain Resume only.
  const steerable = item.steerable !== false;
  const what = item.current_node_id ? ` at ${item.current_node_id}` : "";
  const resume = async (withSteer: boolean) => {
    setBusy(true);
    setError(null);
    const s = withSteer ? steer.trim() : "";
    const r = target && s ? await act.resume(item.id, null, { [target]: s }) : await act.resume(item.id, s || null);
    setBusy(false);
    if (r.ok) reload();
    else setError(r.error);
  };
  return (
    <section className="item-card is-neutral" aria-label="Paused">
      <h2 className="item-card-head"><Pause size={14} aria-hidden /> <span className="item-card-title">Paused</span><span className="item-card-where">{what.trim()}</span></h2>
      {steerable && <textarea aria-label="Steer" className="item-input" rows={2} placeholder="Steer the next agent (optional)" value={steer} onChange={(e) => setSteer(e.target.value)} onKeyDown={sendOnModEnter(() => resume(true), !busy && !!steer.trim())} />}
      {steerable && paused.length > 1 && (
        <label className="item-check">
          steer goes to
          <select className="item-select" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">every paused task</option>
            {paused.map((s) => <option key={s.id} value={s.hook_point}>{taskName(s.hook_point)}</option>)}
          </select>
        </label>
      )}
      {error && <p className="item-error" role="alert">{error}</p>}
      <div className="item-actions">
        {steerable && <Button variant="primary" disabled={busy || !steer.trim()} onClick={() => resume(true)}>Resume with steer</Button>}
        <Button variant={steerable ? "secondary" : "primary"} disabled={busy} onClick={() => resume(false)}>Resume</Button>
      </div>
    </section>
  );
}

/** Words only: Start is the header's main button (the peek's footer), as Resume is for a paused item. */
function NotStartedCard({ item }: { item: ItemDetail }) {
  const first = item.chain_definition.nodes[0]?.id;
  return (
    <section className="item-card is-neutral" aria-label="Not started">
      <h2 className="item-card-head"><Clock size={14} aria-hidden /> <span className="item-card-title">Not started</span></h2>
      <p className="item-muted">Nothing has run, and nothing spends tokens until you start it.{first ? ` Start runs it from ${first}.` : ""}</p>
    </section>
  );
}
