import { ChevronRight, Pencil, Plus, type LucideIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { showToast } from "../../ui/Toast";
import { notListed, unlisted } from "../../ui/Combobox";
import { ChoiceSheet, EditSheet, useSheet, type Listed, type Option } from "../nav/Sheet";
import "./areas.css";

/** One row of an area (the prototype's row): a label with a sub line, a value, and what a tap does. */
export interface RowSpec {
  key?: string;
  label: string;
  sub?: string;
  value?: string;
  mono?: boolean;
  /** The draft differs from what is published: the value turns amber, with the word for a screen reader. */
  changed?: boolean;
  /** A tap opens an edit sheet (the ✎ shows). */
  onEdit?: () => void;
  /** A tap goes to another screen (the chevron shows). */
  to?: string;
  /** A switch row: the whole row is the control. */
  sw?: boolean;
  onSwitch?: (on: boolean) => void;
  chips?: { label: string; tone?: "ok" | "warn" | "bad" }[];
  icon?: LucideIcon;
  disabled?: boolean;
  danger?: boolean;
  onClick?: () => void;
  /** A save-on-change row's refusal, in words, under the label. */
  error?: string;
  /** Something to draw before the label (a swatch). */
  lead?: ReactNode;
  /** One of a set of rows that is the choice: a radio, with a ✓ when it is the one. */
  selected?: boolean;
}

function RowBody({ r }: { r: RowSpec }) {
  const Icon = r.icon;
  return (
    <>
      {r.lead}
      {Icon && <span className="ph-row-icon"><Icon size={15} aria-hidden="true" /></span>}
      <span className="ph-row-text">
        <span className={`ph-row-label${r.mono ? " ph-mono" : ""}`}>{r.label}</span>
        {r.sub && <span className="ph-row-hint">{r.sub}</span>}
        {r.error && <span className="ph-row-error" role="alert">{r.error}</span>}
      </span>
      {r.selected && <span className="ph-row-check" aria-hidden="true">✓</span>}
      {r.chips && <span className="ph-chips-end">{r.chips.map((c) => <span key={c.label} className={`ph-chipword${c.tone ? ` ph-tone-${c.tone}` : ""}`}>{c.label}</span>)}</span>}
      {r.value != null && r.value !== "" && (
        <span className={`ph-row-value${r.changed ? " ph-is-changed" : ""}${r.mono ? " ph-mono" : ""}`}>
          {r.value}
          {r.changed && <span className="ph-visually-hidden"> (changed)</span>}
        </span>
      )}
      {r.onEdit && <Pencil size={14} className="ph-chev" aria-hidden="true" />}
      {r.to && <ChevronRight size={16} className="ph-chev" aria-hidden="true" />}
    </>
  );
}

export function Row({ r }: { r: RowSpec }) {
  if (r.sw != null)
    return (
      <button type="button" role="switch" aria-checked={r.sw} disabled={r.disabled} className="ph-row ph-switch-row" onClick={() => r.onSwitch?.(!r.sw)}>
        <RowBody r={r} />
        <span className={`ph-switch${r.sw ? " ph-is-on" : ""}${r.changed ? " ph-is-changed" : ""}`} aria-hidden="true"><span className="ph-switch-knob" /></span>
      </button>
    );
  if (r.to) return <Link to={r.to} className="ph-row"><RowBody r={r} /></Link>;
  const act = r.onEdit ?? r.onClick;
  if (act) return <button type="button" role={r.selected != null ? "radio" : undefined} aria-checked={r.selected} disabled={r.disabled} className={`ph-row${r.danger ? " ph-is-danger" : ""}`} onClick={act}><RowBody r={r} /></button>;
  return <div className="ph-row ph-row-static"><RowBody r={r} /></div>;
}

/** A group: a small-caps head with an optional + Add, a note, a bordered list of rows, a footnote. */
export function Group({ title, add, note, rows, foot, children }: { title?: string; add?: { label: string; run: () => void }; note?: string; rows?: RowSpec[]; foot?: string; children?: ReactNode }) {
  return (
    <section className="ph-group-block" aria-label={title}>
      {(title || add) && (
        <div className="ph-block-head">
          {title && <h2>{title}</h2>}
          {add && <button type="button" className="ph-add-btn" onClick={add.run}><Plus size={14} aria-hidden="true" />{add.label}</button>}
        </div>
      )}
      {note && <p className="ph-note">{note}</p>}
      {rows && rows.length > 0 && <div className="ph-list">{rows.map((r, i) => <Row key={r.key ?? `${r.label}:${i}`} r={r} />)}</div>}
      {children}
      {foot && <p className="ph-note">{foot}</p>}
    </section>
  );
}

/** What an edit sheet asks: a choice of options, or text. `set` answers the server's refusal, or null once it took. */
export type Edit =
  | { kind: "menu"; title: string; help?: string; options: Option[]; pick: (v: string) => Edit | Promise<string | null> }
  | { kind: "choice"; title: string; help?: string; options: Option[]; value: string | null; set: (v: string) => Promise<string | null> }
  | { kind: "text"; title: string; help?: string; value: string; placeholder?: string; secret?: boolean; multiline?: boolean; submit?: string; set: (v: string) => Promise<string | null>; listed?: Listed };

/** The one edit sheet of an area: `edit(spec)` opens it, a refusal stays inside it, a taken value closes it. */
export function useEditor() {
  const sheet = useSheet();
  const [spec, setSpec] = useState<Edit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const edit = useCallback((e: Edit) => {
    setSpec(e);
    setError(null);
    sheet.open("edit");
  }, [sheet]);
  const submit = async (v: string) => {
    if (!spec || spec.kind === "menu") return;
    const l = spec.kind === "text" ? spec.listed : undefined;
    const stray = l?.closed && l.choices.length ? notListed(unlisted(v, l.choices, l.multiple), l.noun) : null;
    if (stray) return setError(stray);
    setBusy(true);
    setError(null);
    const err = await spec.set(v);
    setBusy(false);
    if (!err) return sheet.close();
    // A choice sheet has no line for the refusal: say it, and leave the control as it was.
    if (spec.kind === "choice") {
      showToast(err);
      sheet.close();
    } else setError(err);
  };
  const pick = async (m: Extract<Edit, { kind: "menu" }>, v: string) => {
    const next = m.pick(v);
    if (!("then" in next)) {
      setSpec(next);
      setError(null);
      return;
    }
    const err = await next;
    if (err) showToast(err);
    sheet.close();
  };
  const node =
    sheet.is("edit") && spec ? (
      spec.kind === "menu" ? (
        <ChoiceSheet title={spec.title} text={spec.help} options={spec.options} onPick={(v) => void pick(spec, v)} onClose={sheet.close} />
      ) : spec.kind === "choice" ? (
        <ChoiceSheet title={spec.title} text={spec.help} options={spec.options} value={spec.value} onPick={(v) => void submit(v)} onClose={sheet.close} />
      ) : (
        <EditSheet title={spec.title} text={spec.help} initial={spec.value} placeholder={spec.placeholder} secret={spec.secret} multiline={spec.multiline} listed={spec.listed?.choices.length ? spec.listed : undefined} submitLabel={spec.submit ?? "Set"} error={error} busy={busy} onSubmit={(v) => void submit(v)} onClose={sheet.close} />
      )
    ) : null;
  return { edit, node, sheet };
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Save on change (Decisions §13): each control runs its own write; a refusal is kept per key to show under that row, a success shows `saved` beside it for a moment. */
export function useSaves() {
  const [errors, setErrors] = useState<Record<string, string | null>>({});
  const [saved, setSaved] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const run = useCallback(async (key: string, write: () => Promise<unknown>): Promise<string | null> => {
    setErrors((e) => ({ ...e, [key]: null }));
    try {
      await write();
      setSaved(key);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setSaved(null), 1800);
      return null;
    } catch (e) {
      setErrors((x) => ({ ...x, [key]: message(e) }));
      return message(e);
    }
  }, []);
  /** The row fields a save leaves behind: the refusal, or `saved`. */
  const sheet = useSheet();
  // Under an open sheet the refusal is the sheet's to show, so it is not announced twice.
  const mark = (key: string): Pick<RowSpec, "error" | "chips"> => ({ ...(errors[key] && sheet.openId === null ? { error: errors[key]! } : {}), ...(saved === key ? { chips: [{ label: "saved", tone: "ok" as const }] } : {}) });
  return { run, mark, errors };
}
