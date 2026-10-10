import { EllipsisVertical, MessageSquare } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { DOLLARS_HINT, dollars, dollarsText, elapsedBetween, plural, shortId } from "../../../format";
import type { KraftEvent, WorkerSession } from "../../../types";
import { withCode } from "../../item/cause";
import { actionPath } from "../../item/paths";
import { act, draftToStart } from "../../item/actions";
import { lines as draftLines } from "../../item/draft/view";
import { isEscalation } from "../../item/nodeGraph";
import { budgetRaise, headerState, neverStarted } from "../../item/status";
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
import { tip } from "../../ui/Tooltip";

const TAG_TONE = { running: "info", waiting: "info", queued: "info", blocked: "info", escalated: "warn", needs_you: "warn", failed: "bad", paused: "warn", done: "ok", cancelled: "muted", archived: "muted" } as const;

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
  const [startLines, setStartLines] = useState<string[]>([]);
  const status = item.display_status ?? "running";
  const hs = headerState(item);
  const bar = pairOf(item);
  const ended = status === "done" || status === "cancelled" || status === "archived";
  const session = currentSession(item);
  const live = session?.status === "running" || session?.status === "pending";
  const { lines } = useLog(session && !ended ? session.id : null, live);
  // Nothing to compare before a start, or once the worktree is gone (archived: R12b-11); the server answers 409 or 404.
  const compare = useCompare(item.id, "base", "latest", false, item.head_sha, neverStarted(item) || item.worktree_exists === false);
  const files = compare.state === "ready" ? (compare.data.files ?? []) : [];
  const card = cardOf(item, events, files.length);
  const adds = files.reduce((n, f) => n + f.insertions, 0);
  const dels = files.reduce((n, f) => n + f.deletions, 0);
  const node = item.chain_definition.nodes.find((n) => n.id === (item.stop?.node ?? item.current_node_id));
  const text = item.description?.trim() ?? "";
  const longBrief = text.length > 140 || text.includes("\n");
  const gate = item.pending_gate ?? item.stop?.node ?? "";

  const go = (id: ActId) => {
    switch (id) {
      case "pause": return sheet.open("pause");
      case "raise": return sheet.open(item.stop?.kind === "cap" ? "raise-cap" : "raise");
      case "steer": case "reject": case "answer": case "escalate": case "cancel": case "complete": return navigate(itemUrl(item.id, `?compose=${id}`));
      case "review": return navigate(reviewUrl(item.id, gate ? `?gate=${encodeURIComponent(gate)}` : ""));
      case "conflicts": return navigate(reviewUrl(item.id, item.stop?.node ? `?nodes=${encodeURIComponent(item.stop.node)}` : ""));
      case "unblock": return void run(act.unblock(item.id), "Unblocked. It starts when a slot is free.");
      case "resume": return void run(act.resume(item.id), "Resumed.");
      // Start never applies a draft: with one, the sheet asks first, as the desktop's Review & apply does.
      case "start": return void draftToStart(item.id).then(({ waits, ops }) => {
        // The sheet lists what it would apply, as the desktop's dialog does (R10b-12).
        setStartLines(draftLines(ops as Parameters<typeof draftLines>[0]).map((l) => l.text));
        if (waits) sheet.open("start-draft");
        else void run(act.resume(item.id), "Started.");
      });
      case "retry": return void run(act.retry(item.id, node ? { path: actionPath(node, item.stop?.task) } : {}), "Retrying.");
      case "reopen-mr": return void run(act.reopenMr(item.id), "MR reopened.");
      case "restore": return void run(act.restore(item.id), "Restored.");
      case "board": return navigate("/");
    }
  };
  // `?raise=1`, from the board's Raise cap / Raise budget: the address is cleaned first, then the sheet opens over this screen,
  // so Back from the sheet lands here. An item that cannot raise the stop just shows its card, which says why.
  const [params, setParams] = useSearchParams();
  const asked = useRef(false);
  useEffect(() => {
    if (params.get("raise") === "1") {
      asked.current = true;
      setParams((p) => { const n = new URLSearchParams(p); n.delete("raise"); return n; }, { replace: true });
    } else if (asked.current) {
      asked.current = false;
      if (bar.primary?.id === "raise") go("raise");
    }
    // Once per arrival: the param is gone after this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);
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
        trailing={<button type="button" className="ph-icon-btn" {...tip("More actions")} onClick={() => sheet.open("kebab")}><EllipsisVertical size={18} aria-hidden="true" /></button>}
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
          {!!item.attachments?.length && (
            <p className="ph-attached">
              <span className="ph-item-meta">attached</span>
              {item.attachments.map((a) => (
                <button key={a.kind} type="button" className="ph-linkbtn" onClick={() => navigate(itemUrl(item.id, `?attached=${encodeURIComponent(a.kind)}`))}>
                  {a.kind} <span className="ph-underline">{a.path.split("/").at(-1)}</span>
                </button>
              ))}
            </p>
          )}
          {files.length > 0 && (
            <button type="button" className="ph-linkbtn ph-diff-link" onClick={() => navigate(reviewUrl(item.id, gate && item.pending_gate ? `?gate=${encodeURIComponent(gate)}` : ""))}>
              <span>{plural(files.length, "file")}</span>
              <span className="ph-add">+{adds}</span>
              <span className="ph-del">−{dels}</span>
              <span aria-hidden="true">·</span>
              <span className="ph-underline">Review changes</span>
            </button>
          )}
        </div>
        {card && (
          <section className={`ph-statecard ph-tone-${card.tone}`} aria-label={card.title}>
            <h2 className="ph-statecard-title">{card.icon && <MessageSquare size={15} className="ph-statecard-icon" aria-hidden="true" />}{card.title}</h2>
            {card.where && <p className="ph-statecard-where">{card.where}</p>}
            {card.text && <p className="ph-statecard-text">{card.text}</p>}
            {card.hint && <p className="ph-statecard-text ph-statecard-fix">{withCode(card.hint)}</p>}
            {card.facts.length > 0 && (
              <dl className="ph-facts">
                {card.facts.map(([k, v, to], i) => <div key={i} className="ph-fact"><dt>{k}</dt><dd>{to ? <button type="button" className="ph-linkbtn ph-factlink ph-underline" onClick={() => navigate(itemUrl(to))}>{v}</button> : v}</dd></div>)}
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
      <ItemSheets item={item} node={node?.id ?? null} sheet={sheet} reload={reload} startLines={startLines} />
    </>
  );
}

/** The sheets over the item: pause, ⋮, and the budget raise. */
function ItemSheets({ item, node, sheet, reload, startLines = [] }: { item: ItemDetail; node: string | null; sheet: ReturnType<typeof useSheet>; reload: () => void; startLines?: string[] }) {
  const { busy, run } = useDo(reload);
  // One start per sheet: a second tap while the first is in flight sends nothing.
  const starting = useRef(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setError(null), [sheet.openId]);
  const cap = item.budget_cap?.cap_usd ?? 0;
  const kebab = useMemo(() => kebabOf(item), [item]);

  if (sheet.is("pause")) return <PauseSheet item={item} node={node} sheet={sheet} reload={reload} />;
  // Start found a draft (R9b-01 on the phone): what the desktop's Review & apply asks, here.
  if (sheet.is("start-draft"))
    return (
      <ChoiceSheet
        title="Start with unapplied changes?"
        text="This item's draft holds changes Start does not apply. Apply them now, or start without them and they stay in the draft."
        options={[{ value: "apply", label: "Apply and start" }, { value: "without", label: "Start without them" }]}
        children={startLines.length > 0 && <pre className="ph-start-lines" aria-label="Changes">{startLines.join("\n")}</pre>}
        onPick={async (v) => {
          if (starting.current) return;
          starting.current = true;
          try {
            if (v === "apply") {
              const a = await act.applyDraft(item.id);
              if (!a.ok) return void showToast(a.error);
            }
            const r = await act.resume(item.id);
            if (!r.ok) return void showToast(r.error);
            showToast(v === "apply" ? "Applied the draft and started." : "Started. The draft is kept.");
            sheet.close();
            reload();
          } finally {
            starting.current = false;
          }
        }}
        onClose={sheet.close}
      />
    );
  if (sheet.is("kebab"))
    return (
      <ChoiceSheet
        title={item.bead_id || shortId(item.id)}
        options={kebab.map((k) => ({ value: k.id, label: k.label, danger: k.danger }))}
        onPick={async (id) => {
          switch (id) {
            case "repo": return sheet.goTo(`/settings/repos/${encodeURIComponent(item.repo)}`);
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
  // A policy budget_usd stopped it, not the item's own cap: /budget/raise would refuse, so raise the policy like any cap.
  // A cap the item cannot raise opens no sheet: the bar offers Retry instead.
  const raise = budgetRaise(item);
  if (sheet.is("raise") && raise === "limit") return <RaiseCapSheet item={item} sheet={sheet} reload={reload} />;
  if (sheet.is("raise") && raise === "item")
    return (
      <ChoiceSheet
        title="Raise budget"
        text={`${(item.stop?.reason ?? "The budget ran out").replace(/\.+$/, "")}. Raising it applies to this item only, and resumes it at once.`}
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
        inputMode="decimal"
        text="The item's new spend cap. It can't go above the policy maximum."
        initial={cap ? String(cap) : ""}
        submitLabel="Raise and resume"
        error={error}
        busy={busy}
        onSubmit={async (v) => {
          // As the desktop's editor reads it: "Infinity" took the cap off, "1,000" is a thousand or one (r12 review).
          const n = dollars(v);
          if (!(n > 0)) return setError(Number.isNaN(n) && v.trim() ? DOLLARS_HINT : "Enter a dollar amount above 0.");
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
  const money = !!words.money;
  const show = (v: number) => (money ? `$${v}` : String(v));
  const submit = async (text: string) => {
    // Dollars are read one way everywhere: Number() took "0x10" as $16 and "1e3" as a thousand (R13b-01).
    // Whole numbers are digits only too: Number() took "0x10" as 16 and "1e1" as 10.
    const n = money ? dollars(text) : /^\d+$/.test(text.trim()) ? Number(text.trim()) : NaN;
    if (money ? !(n > 0) : !Number.isInteger(n) || n <= 0) return setError(money ? (Number.isNaN(n) && text.trim() ? DOLLARS_HINT : "Enter a dollar amount.") : `Enter a whole number of ${words.unit}.`);
    if (n <= limit.value) return setError(`It has to be above the current ${show(limit.value)}.`);
    if (limit.maximum != null && n > limit.maximum) return setError(`The policy maximum is ${show(limit.maximum)}.`);
    setError(null);
    const patched = await act.patch(item.id, limitPatch(item, limit, n));
    if (!patched.ok) return setError(patched.error);
    const r = await run(act.retry(item.id), "Raised. Retrying.");
    if (r.ok) sheet.close();
    else {
      reload();
      setError(`Raised to ${show(n)}, but the retry was refused: ${r.error}`);
    }
  };
  return (
    <EditSheet
      title={`Raise the ${words.noun}`}
      inputMode={money ? "decimal" : "numeric"}
      text={`Now ${money ? show(limit.value) : plural(limit.value, words.one, words.unit)}${where}. ${limit.maximum != null ? `The policy maximum is ${show(limit.maximum)}.` : "The policy sets no maximum."} Applies to this item only, then retries.`}
      initial={money ? dollarsText(limit.value) : String(limit.value)}
      submitLabel="Save & retry"
      error={error}
      busy={busy}
      onSubmit={(v) => void submit(v)}
      onClose={sheet.close}
    />
  );
}
