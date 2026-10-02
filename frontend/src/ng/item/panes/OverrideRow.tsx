import { Pencil, RotateCcw } from "lucide-react";
import { useId, useState } from "react";
import { parse, show, type FieldKind } from "../../templates/fields";
import { IconButton } from "../../ui/IconButton";

type Props = {
  label: string;
  hint?: string;
  kind: FieldKind;
  /** The value set for this item; `undefined` when it is not. */
  own: unknown;
  /** What the chain gives, when it is known; the row says "as the chain gives it" otherwise. */
  inherited?: string | null;
  /** Where `inherited` comes from, as its chip says it: the chain, the policy, the item-wide setting. */
  source?: string;
  /** A closed list: the editor is a select. */
  options?: string[] | null;
  /** An open list: suggested as the person types, any text still allowed. */
  suggest?: string[];
  /** A problem the server named for this row. */
  problem?: string | null;
  /** What a long field's empty editor says it is for. */
  placeholder?: string;
  onSave: (value: unknown) => void;
  onReset: () => void;
};

const word = (v: unknown, kind: FieldKind) => (kind === "bool" ? (v ? "on" : "off") : show(v, kind));

/** One Config row a person overrides for this item in place (Decisions §5
 *  Editing the chain): the value, ✎ to edit it, ↺ to drop the override. An
 *  inherited value says it comes from the chain. */
export function OverrideRow({ label, hint, kind, own: set, inherited, source = "chain", options, suggest, problem, placeholder, onSave, onReset }: Props) {
  const own = set !== undefined;
  const [editing, setEditing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const save = (text: string) => {
    setEditing(false);
    if (text.trim() === "") return void setErr(null);
    if (kind === "bool") return void (setErr(null), onSave(text === "on"));
    const p = parse(text, kind);
    if ("error" in p) return void (setErr(`${label}: ${p.error}`), setEditing(true));
    if (kind === "number" && p.value === 0) return void (setErr(`${label}: A number above 0.`), setEditing(true));
    setErr(null);
    onSave(p.value);
  };
  return (
    <div className={`cfg-row${own ? " is-own" : ""}`}>
      <span className="cfg-k">{label}{hint && <span className="idr-hint"> · {hint}</span>}</span>
      {editing ? <Editor label={label} kind={kind} options={kind === "bool" ? ["on", "off"] : options} suggest={suggest} placeholder={placeholder} onSave={save} onCancel={() => { setEditing(false); setErr(null); }} /> : (
        <span className={`cfg-v${own || inherited ? "" : " is-unset"}${kind === "long" ? " is-prose" : ""}`}>{own ? word(set, kind) : inherited ?? "as the chain gives it"}</span>
      )}
      {own && <span className="cfg-dot" aria-label="overridden for this item" />}
      {own ? <span className="cfg-chip is-own">this item</span> : inherited && !editing && <span className="cfg-chip" title={source === "item-wide" ? "set for every agent task of this item" : `from the ${source}`}>{source}</span>}
      {!editing && (
        <>
          <IconButton label={`Override ${label}`} onClick={() => setEditing(true)}><Pencil size={12} aria-hidden /></IconButton>
          {own ? <IconButton label={`Reset ${label}`} onClick={onReset}><RotateCcw size={12} aria-hidden /></IconButton> : <span className="cfg-gap" />}
        </>
      )}
      {(err || problem) && <p className="cfg-err" role="alert">{err ?? problem}</p>}
    </div>
  );
}

function Editor({ label, kind, options, suggest, placeholder, onSave, onCancel }: { label: string; kind: FieldKind; options?: string[] | null; suggest?: string[]; placeholder?: string; onSave: (t: string) => void; onCancel: () => void }) {
  const [text, setText] = useState("");
  const list = useId();
  const keys = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.stopPropagation(); onCancel(); }
    if (e.key === "Enter" && !(kind === "long" && e.shiftKey)) { e.preventDefault(); onSave(text); }
  };
  if (options?.length)
    return (
      <select autoFocus aria-label={label} className="cfg-edit" value={text} onKeyDown={keys} onBlur={() => onSave(text)} onChange={(e) => onSave(e.target.value)}>
        <option value="">not set</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  if (kind === "long") return <textarea autoFocus aria-label={label} className="cfg-edit is-long" rows={4} placeholder={placeholder} value={text} onKeyDown={keys} onBlur={() => onSave(text)} onChange={(e) => setText(e.target.value)} />;
  return (
    <>
      <input autoFocus aria-label={label} className="cfg-edit" value={text} spellCheck={false} list={suggest?.length ? list : undefined} placeholder={kind === "minutes" ? "minutes" : undefined} onKeyDown={keys} onBlur={() => onSave(text)} onChange={(e) => setText(e.target.value)} />
      {!!suggest?.length && <datalist id={list}>{suggest.map((s) => <option key={s} value={s} />)}</datalist>}
    </>
  );
}
