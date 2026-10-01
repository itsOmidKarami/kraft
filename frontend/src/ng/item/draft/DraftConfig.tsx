import { Pencil, RotateCcw } from "lucide-react";
import { useState } from "react";
import { parse, show } from "../../templates/fields";
import { Head, Note } from "../../templates/panes/controls";
import { useHarnessOptions } from "../../templates/panes/useHarnessOptions";
import { IconButton } from "../../ui/IconButton";
import { useDraft } from "./context";
import { fieldsFor, type DraftField } from "./fields";
import type { Op } from "./types";
import { overrideOf, setField } from "./view";

/** The Config rows ✎ makes overrides on, for a node, step or task that has not
 *  run (Decisions §5 Editing the chain). The inherited value is not known to the
 *  item API (Kraft-jmofl), so a row says "as the chain gives it" until it is
 *  overridden, and an editor starts empty (Decided 7). */
export function DraftConfig({ path, saying }: { path: string; saying?: string }) {
  const d = useDraft();
  if (!d || d.draft.status !== "ready") return null;
  const node = path.split(".")[0];
  if (!d.editable(node)) return <Note>{saying ?? "Already run or running: edit a later node."}</Note>;
  const fields = fieldsFor(path);
  return (
    <>
      <Head>Override for this item</Head>
      <div className="cfg">{fields.map((f) => <Row key={`${f.group}.${f.key}`} f={f} path={path} />)}</div>
      {path.split(".").length === 3 && <Note>The run reads an override when it reaches this task. A prompt override replaces the whole prompt for this item.</Note>}
    </>
  );
}

function Row({ f, path }: { f: DraftField; path: string }) {
  const d = useDraft()!;
  const op = overrideOf(d.ops, path);
  const set = op?.[f.group]?.[f.key];
  const own = set !== undefined;
  const [editing, setEditing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const problem = d.issues.find((i) => i.path === path && !i.passed && opHolds(d.ops[i.index], f));
  const save = (text: string) => {
    setEditing(false);
    if (text.trim() === "") return void setErr(null);
    const p = parse(text, f.kind);
    if ("error" in p) return void (setErr(`${f.label}: ${p.error}`), setEditing(true));
    if (f.kind === "number" && p.value === 0) return void (setErr(`${f.label}: A number above 0.`), setEditing(true));
    setErr(null);
    void d.draft.edit((ops) => setField(ops, path, f.group, f.key, p.value), path);
  };
  const reset = () => void d.draft.edit((ops) => setField(ops, path, f.group, f.key, undefined), path);
  return (
    <div className={`cfg-row${own ? " is-own" : ""}`}>
      <span className="cfg-k">{f.label}{f.hint && <span className="idr-hint"> · {f.hint}</span>}</span>
      {editing ? <Editor f={f} onSave={save} onCancel={() => { setEditing(false); setErr(null); }} /> : (
        <span className={`cfg-v${own ? "" : " is-unset"}${f.kind === "long" ? " is-prose" : ""}`}>{own ? show(set, f.kind) : "as the chain gives it"}</span>
      )}
      {own && <span className="cfg-dot" aria-label="overridden for this item" />}
      {own && <span className="cfg-chip is-own">this item</span>}
      {!editing && (
        <>
          <IconButton label={`Override ${f.label}`} onClick={() => setEditing(true)}><Pencil size={12} aria-hidden /></IconButton>
          {own ? <IconButton label={`Reset ${f.label}`} onClick={reset}><RotateCcw size={12} aria-hidden /></IconButton> : <span className="cfg-gap" />}
        </>
      )}
      {(err || problem) && <p className="cfg-err" role="alert">{err ?? problem?.message}</p>}
    </div>
  );
}

const opHolds = (op: Op | undefined, f: DraftField) => op?.op === "override" && op[f.group]?.[f.key] !== undefined;

function Editor({ f, onSave, onCancel }: { f: DraftField; onSave: (t: string) => void; onCancel: () => void }) {
  const [text, setText] = useState("");
  const opts = useHarnessOptions();
  const options = f.key === "harness" && typeof opts !== "string" ? opts.harnesses.profiles.map((p) => p.id) : f.key === "effort" ? [...new Set(typeof opts === "string" ? [] : Object.values(opts.providers.valid).flatMap((p) => p.capabilities?.effort?.values ?? []))] : null;
  const keys = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.stopPropagation(); onCancel(); }
    if (e.key === "Enter" && !(f.kind === "long" && e.shiftKey)) { e.preventDefault(); onSave(text); }
  };
  if (options?.length)
    return (
      <select autoFocus aria-label={f.label} className="cfg-edit" value={text} onKeyDown={keys} onBlur={() => onSave(text)} onChange={(e) => onSave(e.target.value)}>
        <option value="">not set</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  if (f.kind === "long") return <textarea autoFocus aria-label={f.label} className="cfg-edit is-long" rows={4} placeholder="Replaces the whole prompt for this item" value={text} onKeyDown={keys} onBlur={() => onSave(text)} onChange={(e) => setText(e.target.value)} />;
  return <input autoFocus aria-label={f.label} className="cfg-edit" value={text} spellCheck={false} placeholder={f.kind === "minutes" ? "minutes" : undefined} onKeyDown={keys} onBlur={() => onSave(text)} onChange={(e) => setText(e.target.value)} />;
}
