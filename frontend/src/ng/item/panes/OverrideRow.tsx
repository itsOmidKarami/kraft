import { Pencil, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { parse, show, type FieldKind } from "../../templates/fields";
import { Combobox } from "../../ui/Combobox";
import { IconButton } from "../../ui/IconButton";
import type { Given } from "../chainValues";

type Props = {
  label: string;
  hint?: string;
  kind: FieldKind;
  /** The value set for this item; `undefined` when it is not. */
  own: unknown;
  /** What applies when this item sets nothing, and where it comes from (its chip);
   *  the row says "as the chain gives it" when that is not known. */
  given?: Given | null;
  /** A closed list: the editor is a select. */
  options?: string[] | null;
  /** An open list: suggested as the person types, any text still allowed. */
  suggest?: string[];
  /** A problem the server named for this row. */
  problem?: string | null;
  /** What a long field's empty editor says it is for. */
  placeholder?: string;
  /** Where a value this item sets lives: saved (`this item`), or only in the
   *  item's draft until Review & apply applies it. */
  scope?: Scope;
  onSave: (value: unknown) => void;
  onReset: () => void;
};

const word = (v: unknown, kind: FieldKind) => (kind === "bool" ? (v ? "on" : "off") : show(v, kind));

/** What a chip's source means, said in full for its title. */
const SOURCES: Record<string, string> = {
  chain: "from this item's chain",
  policy: "from the policy",
  "policy maximum": "the policy's maximum",
  "item policy": "from this item's own policy",
  "item-wide": "set for every agent task of this item",
  node: "set for this node of this item",
  profile: "from the task's agent profile",
  repo: "from the repo's models",
  harness: "the harness's default",
};

export type Scope = "this item" | "draft";
/** What an own value's chip and dot say in full. */
const SCOPES: Record<Scope, string> = {
  "this item": "overridden for this item",
  draft: "in this item's draft, not applied yet",
};

/** One Config row a person overrides for this item in place (Decisions §5
 *  Editing the chain): the value, ✎ to edit it, ↺ to drop the override. A
 *  value this item does not set says where it comes from. */
export function OverrideRow({ label, hint, kind, own: set, given, options, suggest, problem, placeholder, scope = "this item", onSave, onReset }: Props) {
  const own = set !== undefined;
  const [editing, setEditing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const pencil = useRef<HTMLButtonElement>(null);
  // Enter or Escape closes the editor from the keyboard: ✎ takes the focus back, not the page.
  const refocus = useRef(false);
  useEffect(() => {
    if (!editing && refocus.current) pencil.current?.focus();
    refocus.current = false;
  }, [editing]);
  const close = (keys: boolean) => {
    refocus.current = keys;
    setEditing(false);
  };
  const save = (text: string, keys: boolean) => {
    close(keys);
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
      {editing ? <Editor label={label} kind={kind} options={kind === "bool" ? ["on", "off"] : options} suggest={suggest} placeholder={placeholder} onSave={save} onCancel={(keys) => { close(keys); setErr(null); }} /> : (
        <span className={`cfg-v${own || given ? "" : " is-unset"}${kind === "long" ? " is-prose" : ""}`}>{own ? word(set, kind) : given?.value ?? "as the chain gives it"}</span>
      )}
      {own && <span className={`cfg-dot${scope === "draft" ? " is-draft" : ""}`} aria-label={SCOPES[scope]} />}
      {own ? <span className={`cfg-chip is-own${scope === "draft" ? " is-draft" : ""}`} title={SCOPES[scope]}>{scope}</span> : given && !editing && <span className="cfg-chip" title={SOURCES[given.source] ?? given.source}>{given.source}</span>}
      {!editing && (
        <>
          <IconButton ref={pencil} label={`Override ${label}`} onClick={() => setEditing(true)}><Pencil size={12} aria-hidden /></IconButton>
          {own ? <IconButton label={`Reset ${label}`} onClick={onReset}><RotateCcw size={12} aria-hidden /></IconButton> : <span className="cfg-gap" />}
        </>
      )}
      {(err || problem) && <p className="cfg-err" role="alert">{err ?? problem}</p>}
    </div>
  );
}

/** The value's editor. A pick or a typed value is kept only on Enter or when
 *  the editor loses focus, so stepping through a list with the arrows saves nothing. */
function Editor({ label, kind, options, suggest, placeholder, onSave, onCancel }: { label: string; kind: FieldKind; options?: string[] | null; suggest?: string[]; placeholder?: string; onSave: (t: string, keys: boolean) => void; onCancel: (keys: boolean) => void }) {
  const [text, setText] = useState("");
  // Enter and Escape already closed it: the blur that follows is not a second save.
  const done = useRef(false);
  const keys = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.stopPropagation(); done.current = true; onCancel(true); }
    if (e.key === "Enter" && !(kind === "long" && e.shiftKey)) { e.preventDefault(); done.current = true; onSave(text, true); }
  };
  const blur = () => { if (!done.current) onSave(text, false); };
  if (options?.length)
    return (
      <select autoFocus aria-label={label} className="cfg-edit" value={text} onKeyDown={keys} onBlur={blur} onChange={(e) => setText(e.target.value)}>
        <option value="">not set</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  if (kind === "long") return <textarea autoFocus aria-label={label} className="cfg-edit is-long" rows={4} placeholder={placeholder} value={text} onKeyDown={keys} onBlur={blur} onChange={(e) => setText(e.target.value)} />;
  if (suggest?.length)
    return (
      <Combobox
        autoFocus
        aria-label={label}
        className="cfg-edit"
        value={text}
        choices={suggest.map((value) => ({ value }))}
        listLabel={`Known ${label}s`}
        onChange={setText}
        // A pick is kept as Enter keeps a typed value.
        onPick={(t) => { done.current = true; onSave(t, true); }}
        onKeyDown={keys}
        onBlur={blur}
      />
    );
  return <input autoFocus aria-label={label} className="cfg-edit" value={text} spellCheck={false} placeholder={kind === "minutes" ? "minutes" : undefined} onKeyDown={keys} onBlur={blur} onChange={(e) => setText(e.target.value)} />;
}
