import { EllipsisVertical } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { elapsedBetween, shortId } from "../../../format";
import type { KraftEvent, WorkerSession } from "../../../types";
import { actionPath } from "../../item/paths";
import { act } from "../../item/actions";
import { isEscalation } from "../../item/nodeGraph";
import { headerState } from "../../item/status";
import { pathSel, placeUrl } from "../../item/url";
import type { ItemDetail } from "../../item/useItem";
import { useCompare } from "../../review/useReview";
import { Button } from "../../ui/Button";
import { showToast } from "../../ui/Toast";
import { LogLines } from "../log/LogLines";
import { useLog } from "../log/useLog";
import { ChoiceSheet, EditSheet, useSheet } from "../nav/Sheet";
import { ScreenHeader } from "../nav/ScreenHeader";
import { ActionBar, Block } from "../ui/Rows";
import { ChainList } from "./ChainList";
import { PauseSheet } from "./PauseSheet";
import { cardOf, kebabOf, limitPatch, limitWords, pairOf, stopLimitOf, type Act, type ActId } from "./model";
import { useDo } from "./useDo";
import "./item.css";

const TAG_TONE = { running: "info", waiting: "info", escalated: "warn", needs_you: "warn", failed: "bad", paused: "warn", done: "ok", cancelled: "muted", archived: "muted" } as const;

/** The session whose log the screen previews: the latest work session on the node the item stands on. */
export function currentSession(item: ItemDetail): WorkerSession | null {
  const node = item.stop?.node ?? item.current_node_id;
  const ss = item.worker_sessions.filter((s) => s.node_id === node && !isEscalation(s)).sort((a, b) => a.created_at.localeCompare(b.created_at));
  return ss.at(-1) ?? null;
}

export const itemUrl = (id: string, search = "") => `/work-items/${encodeURIComponent(id)}${search}`;
export const reviewUrl = (id: string, q = "") => `/work-items/${encodeURIComponent(id)}/review${q}`;

/** `/work-items/:id` (W17 brief C): the brief, the state card, the chain, a log
 *  preview and the one pair of buttons. */
export function ItemScreen({ item, events, reload, now }: { item: ItemDetail; events: KraftEvent[]; reload: () => void; now: number }) {
  const navigate = useNavigate();
  const sheet = useSheet();
  const { busy, run } = useDo(reload);
  const [brief, setBrief] = useState(false);
  const status = item.display_status ?? "running";
  const hs = headerState(item);
  const card = cardOf(item, events);
  const bar = pairOf(item);
  const limit = stopLimitOf(item);
  const ended = status === "done" || status === "cancelled" || status === "archived";
  const session = currentSession(item);
  const live = session?.status === "running" || session?.status === "pending";
  const { lines } = useLog(session && !ended ? session.id : null, live);
  const compare = useCompare(item.id, "base", "latest", false, item.head_sha);
  const files = compare.state === "ready" ? (compare.data.files ?? []) : [];
  const adds = files.reduce((n, f) => n + f.insertions, 0);
  const dels = files.reduce((n, f) => n + f.deletions, 0);
  const node = item.chain_definition.nodes.find((n) => n.id === (item.stop?.node ?? item.current_node_id));
  const text = item.description?.trim() ?? "";
  const longBrief = text.length > 140 || text.includes("\n");
  const gate = item.pending_gate ?? item.stop?.node ?? "";

  const go = (id: ActId) => {
    switch (id) {
      case "pause": return sheet.open("pause");
      case "raise": return sheet.open("raise");
      case "steer": case "reject": case "answer": case "escalate": case "cancel": case "complete": return navigate(itemUrl(item.id, `?compose=${id}`));
      case "review": return navigate(reviewUrl(item.id, gate ? `?gate=${encodeURIComponent(gate)}` : ""));
      case "conflicts": return navigate(reviewUrl(item.id, item.stop?.node ? `?nodes=${encodeURIComponent(item.stop.node)}` : ""));
      case "resume": case "start": return void run(act.resume(item.id), id === "start" ? "Started." : "Resumed.");
      case "retry": return void run(act.retry(item.id, node ? { path: actionPath(node, item.stop?.task) } : {}), "Retrying.");
      case "retry-now": return void run(act.retry(item.id), "Retrying.");
      case "reopen-mr": return void run(act.reopenMr(item.id), "MR reopened.");
      case "restore": return void run(act.restore(item.id), "Restored.");
      case "board": return navigate("/");
    }
  };
  const button = (a: Act | null, primary: boolean) =>
    a && (
      <Button key={a.id} className={`ph-btn${primary ? " ph-btn-primary" : ""}`} variant={a.danger ? "danger" : primary ? "primary" : "secondary"} disabled={busy} onClick={() => go(a.id)}>
        {a.label}
      </Button>
    );

  return (
    <>
      <ScreenHeader
        id={item.bead_id || shortId(item.id)}
        trailing={<button type="button" className="ph-icon-btn" aria-label="More actions" onClick={() => sheet.open("kebab")}><EllipsisVertical size={18} aria-hidden="true" /></button>}
      />
      <div className="ph-content">
        <div className="ph-item-top">
          <div className="ph-item-tagline">
            <span className={`ph-tag ph-tag-${TAG_TONE[status]}`}>{hs.badge}</span>
            <span className="ph-item-meta">{elapsedBetween(item.created_at, ended ? item.updated_at : null, now)} · {item.repo.split("/").filter(Boolean).at(-1)}</span>
          </div>
          <h1 className="ph-item-title">{item.title}</h1>
          {text && <p className={`ph-brief${brief ? " ph-is-open" : ""}`}>{text}</p>}
          {longBrief && <button type="button" className="ph-linkbtn" aria-expanded={brief} onClick={() => setBrief(!brief)}>{brief ? "less" : "more"}</button>}
          {files.length > 0 && (
            <button type="button" className="ph-linkbtn ph-diff-link" onClick={() => navigate(reviewUrl(item.id, gate && item.pending_gate ? `?gate=${encodeURIComponent(gate)}` : ""))}>
              <span>{files.length} {files.length === 1 ? "file" : "files"}</span>
              <span className="ph-add">+{adds}</span>
              <span className="ph-del">−{dels}</span>
              <span aria-hidden="true">·</span>
              <span className="ph-underline">Review changes</span>
            </button>
          )}
        </div>
        {card && (
          <section className={`ph-statecard ph-tone-${card.tone}`} aria-label={card.title}>
            <h2 className="ph-statecard-title">{card.title}</h2>
            {card.where && <p className="ph-statecard-where">{card.where}</p>}
            {card.text && <p className="ph-statecard-text">{card.text}</p>}
            {limit && <button type="button" className="ph-linkbtn" onClick={() => sheet.open("raise-cap")}>Raise the {limitWords(limit).noun}…</button>}
            {card.facts.length > 0 && (
              <dl className="ph-facts">
                {card.facts.map(([k, v]) => <div key={k} className="ph-fact"><dt>{k}</dt><dd>{v}</dd></div>)}
              </dl>
            )}
          </section>
        )}
        <ChainList item={item} events={events} now={now} />
        {session && lines && lines.length > 0 && !ended && (
          <Block
            title="Log"
            aside={<button type="button" className="ph-linkbtn" onClick={() => {
              const sel = pathSel(session.hook_point, item.chain_definition.nodes);
              navigate(placeUrl(item.id, { node: session.node_id, sel: sel ?? { kind: "node", node: session.node_id }, tab: "log" }));
            }}>Show all</button>}
          >
            <p className="ph-log-path">{session.hook_point.split(".").join(" › ")}</p>
            <LogLines lines={lines.slice(-5)} empty="" />
          </Block>
        )}
      </div>
      {(bar.secondary || bar.primary) && <ActionBar>{button(bar.secondary, false)}{button(bar.primary, true)}</ActionBar>}
      <ItemSheets item={item} node={node?.id ?? null} sheet={sheet} reload={reload} />
    </>
  );
}

/** The sheets over the item: pause, ⋮, and the budget raise. */
function ItemSheets({ item, node, sheet, reload }: { item: ItemDetail; node: string | null; sheet: ReturnType<typeof useSheet>; reload: () => void }) {
  const { busy, run } = useDo(reload);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setError(null), [sheet.openId]);
  const cap = item.budget_cap?.cap_usd ?? 0;
  const kebab = useMemo(() => kebabOf(item), [item]);

  if (sheet.is("pause")) return <PauseSheet item={item} node={node} sheet={sheet} reload={reload} />;
  if (sheet.is("kebab"))
    return (
      <ChoiceSheet
        title={item.bead_id || shortId(item.id)}
        options={kebab.map((k) => ({ value: k.id, label: k.label, danger: k.danger }))}
        onPick={async (id) => {
          switch (id) {
            case "settings": return node ? sheet.goTo(placeUrl(item.id, { node, sel: { kind: "node", node }, tab: "config" })) : sheet.close();
            case "open-mr": sheet.close(); return void window.open(item.mr_ref?.url, "_blank", "noopener");
            case "duplicate": {
              const r = await act.duplicate(item.id);
              if (!r.ok) return void showToast(r.error);
              showToast(r.body.duplicate_warning ?? "Duplicated as a new item, paused.");
              return sheet.goTo(itemUrl(r.body.id));
            }
            case "archive": {
              const r = await act.archive(item.id);
              if (!r.ok) return void showToast(r.error);
              showToast("Archived.");
              return sheet.goTo("/");
            }
            default: return sheet.goTo(itemUrl(item.id, `?compose=${id}`));
          }
        }}
        onClose={sheet.close}
      />
    );
  if (sheet.is("raise"))
    return (
      <ChoiceSheet
        title="Raise budget"
        text={`${item.stop?.reason ?? "The budget ran out"}. Raising it applies to this item only, and resumes it at once.`}
        options={[
          { value: "5", label: `+$5`, hint: `$${cap + 5}` },
          { value: "10", label: `+$10`, hint: `$${cap + 10}` },
          { value: "none", label: "No cap" },
          { value: "amount", label: "Set an amount…" },
        ]}
        onPick={async (v) => {
          if (v === "amount") return sheet.swap("raise-amount");
          const r = await act.raiseBudget(item.id, v === "none" ? null : cap + Number(v));
          if (!r.ok) return void showToast(r.error);
          showToast("Budget raised. The item is running again.");
          sheet.close();
          reload();
        }}
        onClose={sheet.close}
      />
    );
  if (sheet.is("raise-cap")) return <RaiseCapSheet item={item} sheet={sheet} reload={reload} />;
  if (sheet.is("raise-amount"))
    return (
      <EditSheet
        title="Budget in dollars"
        text="The item's new spend cap. It can't go above the policy maximum."
        initial={cap ? String(cap) : ""}
        submitLabel="Raise and resume"
        error={error}
        busy={busy}
        onSubmit={async (v) => {
          const n = Number(v);
          if (!(n > 0)) return setError("Enter a dollar amount above 0.");
          const r = await run(act.raiseBudget(item.id, n), "Budget raised. The item is running again.");
          if (r.ok) sheet.close();
          else setError(r.error);
        }}
        onClose={sheet.close}
      />
    );
  return null;
}

/** Raise the limit that stopped the item, then retry (R73): one number, the same two calls the desktop's editor makes. */
function RaiseCapSheet({ item, sheet, reload }: { item: ItemDetail; sheet: ReturnType<typeof useSheet>; reload: () => void }) {
  const limit = stopLimitOf(item);
  const { busy, run } = useDo(reload);
  const [error, setError] = useState<string | null>(null);
  if (!limit) return null;
  const words = limitWords(limit);
  const where = limit.path ? ` on ${limit.path}` : "";
  const submit = async (text: string) => {
    const n = Number(text.trim());
    if (!Number.isInteger(n) || n <= 0) return setError(`Enter a whole number of ${words.unit}.`);
    if (n <= limit.value) return setError(`It has to be above the current ${limit.value}.`);
    if (limit.maximum != null && n > limit.maximum) return setError(`The policy maximum is ${limit.maximum}.`);
    setError(null);
    const patched = await act.patch(item.id, limitPatch(limit, n));
    if (!patched.ok) return setError(patched.error);
    const r = await run(act.retry(item.id), "Raised. Retrying.");
    if (r.ok) sheet.close();
    else {
      reload();
      setError(`Raised to ${n}, but the retry was refused: ${r.error}`);
    }
  };
  return (
    <EditSheet
      title={`Raise the ${words.noun}`}
      text={`Now ${limit.value} ${words.unit}${where}. ${limit.maximum != null ? `The policy maximum is ${limit.maximum}.` : "The policy sets no maximum."} Applies to this item only, then retries.`}
      initial={String(limit.value)}
      submitLabel="Save & retry"
      error={error}
      busy={busy}
      onSubmit={(v) => void submit(v)}
      onClose={sheet.close}
    />
  );
}
