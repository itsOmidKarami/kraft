import { doneText } from "./chainValues";
import { Link, useNavigate } from "react-router-dom";
import { useEffect, useState, type ReactNode } from "react";
import * as api from "../../api";
import { ago, elapsed, logLineText, plural, until, usd } from "../../format";
import { countedAgainst } from "../settings/storageWords";
import type { ChainNode, DiffFile, KraftEvent, WorkerSession } from "../../types";
import { Ban, CircleCheck, CircleHelp, Clock, Pause, X } from "../icons";
import { Button } from "../ui/Button";
import { act } from "./actions";
import { nameOf } from "../board/rowText";
import { admits, neverStarted, RELEASED, spentLine } from "./status";
import { actionPath, taskName } from "./paths";
import { pathSel, placeUrl } from "./url";
import { useLogLines } from "./useLogLines";
import { totals } from "./Top";
import { FORGE_LOGIN_HINT, failedFix, failureFacts, headline, keptLine, withCode } from "./cause";
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
type Action = { label: string; run: () => unknown; primary?: boolean };
type Card = { tone: "bad" | "info" | "warn" | "muted" | "neutral"; glyph: ReactNode; title: string; where?: string; text?: string; facts: [string, ReactNode][]; actions: Action[]; node?: string | null; body?: ReactNode; note?: string };

/** `action` where `ok`, the server admitting its door (`admits`), else nothing. */
const when = (ok: boolean, action: Action): Action[] => (ok ? [action] : []);

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
  const card = cardFor(item, { ...h, files, logUrl: logLink(item), onRepos: () => navigate(`/settings/repos/${encodeURIComponent(item.repo)}`), onReview: (nodes) => navigate(`/work-items/${encodeURIComponent(item.id)}/review${nodes ? `?nodes=${encodeURIComponent(nodes)}` : ""}`) }, events, async (p) => {
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
          <Button key={a.label} variant={a.primary ? "primary" : "secondary"} disabled={busy} aria-describedby={a.primary && card.note ? `${item.id}-card-note` : undefined} onClick={() => void a.run()}>{a.label}</Button>
        ))}
        {card.node && <button type="button" className="item-link item-card-open" onClick={() => h.onOpenNode(card.node!)}>Open {nameOf(item.chain_definition.nodes, card.node)} →</button>}
      </div>
      {card.note && <p id={`${item.id}-card-note`} className="item-muted item-card-note">{card.note}</p>}
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
  const target = task ? taskName(path ?? task) : (item.stop?.node ? nameOf(item.chain_definition.nodes, item.stop.node) : "the node");
  return { label, target, primary: true, run: () => run(act.retry(item.id, path ? { path } : {})) };
}

/** What the failed card offers besides Escalate, by `failedFix`: a refused forge credential gets words, not a button
 *  (`FORGE_LOGIN_HINT`; the repo's settings hold no token), a stranded claim just a retry, and any other
 *  infrastructure stop, or one naming no cause, a way to the repo's settings. */
function failedActions(item: ItemDetail, retry: ReturnType<typeof retryFrom>, h: { onRepos: () => void }) {
  const retries = when(admits(item, "retry", true), retry);
  return failedFix(item) === "repo" ? [...retries, { label: "Check the repo settings", run: h.onRepos }] : retries;
}

function cardFor(item: ItemDetail, h: Handlers & { files: DiffFile[] | null; onRepos: () => void; onReview: (nodes?: string) => void; logUrl: string }, events: KraftEvent[], run: Run): Card | null {
  const stop = item.stop;
  const facts = (stop?.facts ?? {}) as Record<string, unknown>;
  const node = item.chain_definition.nodes.find((n) => n.id === stop?.node);
  const spent: [string, ReactNode] | null = item.budget_cap ? ["spent", spentLine(item)] : null;
  const where = stop ? [pathOf(stop.task) || (stop.node && nameOf(item.chain_definition.nodes, stop.node)), stop.attempt ? `attempt ${stop.attempt}` : ""].filter(Boolean).join(" · ") : undefined;
  const status = item.display_status;

  if (status === "failed" && stop) {
    const { rows, structured } = failureFacts(item);
    // "Do I lose anything?": the branch and what is on it stay.
    const kept = keptLine(item, h.files?.length);
    const keptFact: [string, ReactNode][] = kept ? [["work kept", kept]] : [];
    // A stop that names no cause says why it failed in the agent's own words: the end of its log.
    const unexplained = !rows.length && !/ — /.test(stop.reason ?? "");
    const retry = retryFrom(item, node, run);
    const retries = admits(item, "retry", true);
    return {
      tone: "bad", glyph: <X size={14} aria-hidden />, title: "Failed", where, text: (structured ? headline(stop.reason) : stop.reason) ?? undefined, node: stop.node,
      body: failedFix(item) === "forge_login" ? <p className="item-card-text item-card-fix">{withCode(FORGE_LOGIN_HINT)}</p> : unexplained ? <FailedTail item={item} to={h.logUrl} /> : undefined,
      facts: [...keptFact, ...rows.slice(0, 3 - keptFact.length), ...(spent ? [spent] : [])],
      actions: [...failedActions(item, retry, h), ...when(admits(item, "escalate", true), { label: "Escalate…", run: h.onEscalate })],
      // What Retry does, in the card's words: the task it restarts. The branch and the spend are already facts above.
      note: retries ? `Restarts ${retry.target}${item.branch ? " on the kept branch" : ""}` : undefined,
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
        ...when(admits(item, "escalate", true), { label: "Send back with guidance", run: h.onEscalate }),
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
      where: [stop.node && nameOf(item.chain_definition.nodes, stop.node), closed ? `closed ${by ? `by ${by} ` : ""}${ago(closed.created_at)}, not merged` : "not merged"].filter(Boolean).join(" · "),
      node: stop.node,
      text: "Kraft stopped syncing the MR. The branch, the review and all findings are intact. Decide whether this work still goes ahead.",
      facts: [],
      actions: [
        ...when(admits(item, "reopen_mr", true), { label: `Reopen !${ref}`, primary: true, run: () => run(act.reopenMr(item.id)) }),
        ...when(!!opened?.node_id && admits(item, "retry", true), { label: "Open a new MR", run: () => run(act.retry(item.id, { path: opened!.node_id! })) }),
        ...when(admits(item, "cancel", true), { label: "Cancel item…", run: h.onCancel }),
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
        ...when(admits(item, "retry", true), retryFrom(item, node, run)),
        ...when(admits(item, "escalate", true), { label: "Escalate…", run: h.onEscalate }),
      ],
    };
  }
  if (status === "queued" && item.queued?.storage) {
    const s = item.queued.storage;
    return {
      tone: "info", glyph: <Clock size={14} aria-hidden />, title: "Queued",
      text: `Waiting for space: ${countedAgainst(s.used_bytes, s.limit_bytes)}. It starts when space frees.`,
      facts: [], actions: [],
    };
  }
  if (status === "done") {
    const mr = item.mr_ref;
    const closed = [...events].reverse().find((e) => e.type === "mr_closed");
    const reopened = [...events].reverse().find((e) => e.type === "mr_reopened");
    const mrClosed = !!closed && (!reopened || reopened.seq < closed.seq);
    const total = h.files?.length ? totals(h.files) : null;
    const took = item.running_time ? elapsed(item.running_time.running_s * 1000) : null;
    const facts: [string, ReactNode][] = [
      ...(mr ? [["merge request", <><a className="item-link" href={mr.url} target="_blank" rel="noreferrer">!{mr.number}</a>{mrClosed ? " · closed on the forge" : ""}</>] as [string, ReactNode]] : []),
      ...(h.files?.length ? [["changes", <>{plural(h.files.length, "file")} <span className="item-add">+{total!.add}</span> <span className="item-del">−{total!.del}</span></>] as [string, ReactNode]] : []),
      ...(spent ? [spent] : []),
      ...(took ? [["took", took] as [string, ReactNode]] : []),
    ];
    return {
      tone: "neutral", glyph: <CircleCheck size={14} aria-hidden />, title: "Done",
      text: doneText(item),
      facts,
      actions: when(admits(item, "archive", true), { label: "Archive", run: () => run(act.archive(item.id)) }),
    };
  }
  if (status === "cancelled") {
    const ev = [...events].reverse().find((e) => e.type === "work_item_cancelled");
    const at = str(ev?.payload.node_id) ?? item.current_node_id;
    return {
      tone: "muted", glyph: <Ban size={14} aria-hidden />, title: "Cancelled", where: at ? `at ${nameOf(item.chain_definition.nodes, at)}` : undefined, node: at,
      text: "The run stopped. Everything it produced is kept. A cancelled item stays cancelled; duplicating it starts a new one with the same title, brief and chain.",
      facts: [
        ["work kept", "the branch, the worktree until it is archived, findings and threads"],
        ...(spent ? [["spent", usd(item.budget_cap!.spent_usd)] as [string, ReactNode]] : []),
        ["reason", str(ev?.payload.reason) || "no reason given"],
      ],
      actions: [
        { label: "Duplicate as new item", primary: true, run: h.onDuplicate },
        ...when(admits(item, "archive", true), { label: "Archive", run: () => run(act.archive(item.id)) }),
      ],
    };
  }
  return null;
}

/** The events a card needs that the detail does not carry (who closed the MR,
 *  which node opened it, the cancel's reason): read once, for those cards only. */
function useEndEvents(item: ItemDetail): KraftEvent[] {
  const wants = item.display_status === "cancelled" || item.display_status === "done" || item.stop?.kind === "mr_closed";
  const [events, setEvents] = useState<KraftEvent[]>([]);
  useEffect(() => {
    if (!wants) return setEvents([]);
    api.getEvents(item.id).then(setEvents, () => setEvents([]));
  }, [item.id, wants, item.updated_at]);
  return events;
}

/** What the item still comes after, each a link to its page, for as long as
 *  one of them is unfinished. Unblock is offered where `/unblock` takes it (a
 *  blocked or paused item) and drops the unfinished ones. A blocked item with
 *  none left stays blocked until the scheduler's next pass, and says so. */
export function ComesAfterCard({ item, reload }: { item: ItemDetail; reload: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const deps = item.dependencies ?? [];
  const waits = deps.filter((d) => !d.met).length;
  const blocked = item.display_status === "blocked";
  if (!waits && !blocked) return null;
  const unblock = async () => {
    setBusy(true);
    setError(null);
    const r = await act.unblock(item.id);
    setBusy(false);
    if (r.ok) reload();
    else setError(r.error);
  };
  return (
    <section className={`item-card is-${blocked ? "info" : "neutral"}`} aria-label={blocked ? "Blocked" : "Comes after"}>
      <h2 className="item-card-head">
        <Clock size={14} aria-hidden /> <span className="item-card-title">{blocked ? "Blocked" : "Comes after"}</span>
        <span className="item-card-where">{waits ? `waiting on ${plural(waits, "item")}` : RELEASED.where}</span>
      </h2>
      <p className="item-card-text">{!waits ? RELEASED.text : blocked ? "Kraft starts it when they complete." : "Started before they complete, it is blocked until they do."}</p>
      <dl className="item-facts item-card-facts">
        {deps.map((d) => (
          <div key={d.id}><dt>{d.status.replace("_", " ")}</dt><dd><Link className="item-link" to={`/work-items/${encodeURIComponent(d.id)}`}>{d.title}</Link></dd></div>
        ))}
      </dl>
      {error && <p className="item-error" role="alert">{error}</p>}
      {waits > 0 && (blocked || item.display_status === "paused") && admits(item, "unblock", true) && (
        <div className="item-actions">
          <Button disabled={busy} onClick={unblock}>Unblock</Button>
        </div>
      )}
    </section>
  );
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
  const resumes = admits(item, "resume", true);
  // No agent task paused at this node: the server refuses a steer, so offer plain Resume only.
  const steerable = item.steerable !== false;
  // One primary: Resume until a steer is typed, then Resume with steer.
  const steered = steerable && !!steer.trim();
  const what = item.current_node_id ? ` at ${nameOf(item.chain_definition.nodes, item.current_node_id)}` : "";
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
        {resumes && <Button variant="primary" disabled={busy} onClick={() => resume(steered)}>{steered ? "Resume with steer" : "Resume"}</Button>}
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

/** Where "Open the full log" goes: the failed task's pane on its Log tab, or the node's when the task is not in the chain. */
function logLink(item: ItemDetail): string {
  const stop = item.stop;
  const sel = stop?.task ? pathSel(stop.task, item.chain_definition.nodes) : null;
  if (sel) return placeUrl(item.id, { sel, tab: "log" });
  return placeUrl(item.id, { sel: stop?.node ? { kind: "node", node: stop.node } : { kind: "chain" } });
}

/** The session the failed stop is about: the stop's task, else the node's newest, an escalation turn apart. */
function failedSession(item: ItemDetail): WorkerSession | null {
  const stop = item.stop;
  if (!stop?.node) return null;
  const onNode = item.worker_sessions.filter((s) => s.node_id === stop.node && !/(^|\.)escalation$/.test(s.hook_point));
  const ofTask = stop.task ? onNode.filter((s) => s.hook_point === stop.task) : [];
  return [...(ofTask.length ? ofTask : onNode)].sort((a, b) => a.created_at.localeCompare(b.created_at)).at(-1) ?? null;
}

const TAIL = 5;

/** The end of the failed task's log, for a stop that names no cause: what the agent last said, and the way to the rest. */
function FailedTail({ item, to }: { item: ItemDetail; to: string }) {
  const session = failedSession(item);
  if (!session) return <p className="item-muted">No session log to show for this stop.</p>;
  return <Tail itemId={item.id} session={session} to={to} />;
}

function Tail({ itemId, session, to }: { itemId: string; session: WorkerSession; to: string }) {
  const { lines, error } = useLogLines(itemId, session.id, session.status === "running");
  const last = (lines ?? []).filter((l) => !l.truncated).map(logLineText).filter((t) => t.trim()).slice(-TAIL);
  return (
    <div className="item-tail">
      {error ? <p className="item-muted">{error}</p>
        : !lines ? <p className="item-muted">Reading the log…</p>
        : !last.length ? <p className="item-muted">The log is empty.</p>
        : <ul className="item-tail-lines" aria-label="Last lines of the log">{last.map((t, i) => <li key={i}>{t}</li>)}</ul>}
      <Link className="item-link" to={to}>Open the full log →</Link>
    </div>
  );
}
