import { useContext, useState } from "react";
import { Pencil, RotateCcw } from "lucide-react";
import { IconButton } from "../../ui/IconButton";
import { Switch } from "../../ui/Switch";
import { authoredAt, authoredNodes, kindOf, resolvedAt, sourceRows, sourceWord } from "../draft/view";
import { fieldMeta, parse, show, type ChoiceSource, type FieldMeta } from "../fields";
import { Head, Note } from "./controls";
import type { PaneKind } from "./describe";
import type { PaneCtx } from "./Overview";
import { effortsFor, useHarnessOptions } from "./useHarnessOptions";
import { Combobox, notListed, unlisted, type Choice } from "../../ui/Combobox";
import { useProviders, type ProviderStatus } from "../../harnesses/useProviders";
import { harnessChoices, steeringChoices } from "../choices";
import { useLibrary } from "../useLibrary";
import { ReadOnly } from "../plugin";
import type { Harnesses } from "../../../types";

/** A row's `source` is absent where there is none to name (a library component's own keys): no chip, no dot, and ↺ removes the key. */
export type Row = { field: string; value: unknown; source?: string; locked?: string; recovery?: boolean };

/** "reject to" goes back to an exec node before this gate. */
function earlierExec(ctx: PaneCtx): string[] {
  const nodes = authoredNodes(ctx.r, ctx.scope);
  const i = nodes.findIndex((n) => n.id === ctx.path);
  return nodes.slice(0, Math.max(0, i)).filter((n) => kindOf(ctx.r, n) === "exec").map((n) => n.id);
}

/** "on base change" restarts from this node or an earlier exec node. */
function restartTargets(ctx: PaneCtx): string[] {
  const nodes = authoredNodes(ctx.r, ctx.scope);
  const i = nodes.findIndex((n) => n.id === ctx.path);
  return nodes.slice(0, i + 1).filter((n) => kindOf(ctx.r, n) === "exec").map((n) => n.id);
}

/** The Config tab (Decisions §9 Config tab, Inherited vs overridden): one list
 *  in the server's order that never regroups; each row its value and source
 *  chip, an override bright with a dot, ✎ to override, ↺ to reset. */
export function Config({ kind, ctx }: { kind: PaneKind; ctx: PaneCtx }) {
  const { r, scope, path } = ctx;
  const readOnly = useContext(ReadOnly);
  if (kind === "chain") return <ChainConfig ctx={ctx} />;
  const rows: Row[] = sourceRows(r, path);
  // A plugin's chain answers no `sources`: what each part sets is in its YAML.
  if (!rows.length && readOnly) return <Note>A plugin's settings are in the YAML tab.</Note>;
  if (!rows.length) return <Note>{r.resolved ? "Nothing to configure here." : "The resolved config shows once the draft resolves."}</Note>;
  // A node with a fix loop cannot be read only (the prototype's lock).
  const node = kind === "node" ? resolvedAt(r, path) : null;
  if (node?.fix_loop) rows.forEach((x) => { if (x.field === "read_only") x.locked = "A node with a fix loop cannot be read only"; });
  return (
    <>
      <Head>Resolved config</Head>
      <div className="cfg">{rows.map((x) => <ConfigRow key={x.field} row={x} ctx={ctx} />)}</div>
      {kind === "task" && <Note>on failure · {authoredAt(r, scope, path)?.on_failure ? "set" : "none"} · edit it in the pane below</Note>}
    </>
  );
}

/** The chain's own caps (the prototype's "Chain-wide time caps"): `sources` has
 *  no row for the chain, so these read the file and say "policy" when unset. */
function ChainConfig({ ctx }: { ctx: PaneCtx }) {
  const policy = (authoredAt(ctx.r, ctx.scope, "")?.policy ?? {}) as Record<string, unknown>;
  const row = (f: string): Row => ({ field: `policy.${f}`, value: policy[f] ?? null, source: policy[f] === undefined ? "policy" : "chain" });
  return (
    <>
      <Head>Chain-wide time caps</Head>
      <div className="cfg">{["time_cap_minutes", "total_time_cap_minutes"].map((f) => <ConfigRow key={f} row={row(f)} ctx={ctx} />)}</div>
      <Head>Fix loops in this chain</Head>
      <div className="cfg">{["max_attempts", "timeout_minutes"].map((f) => <ConfigRow key={f} row={row(f)} ctx={ctx} />)}</div>
    </>
  );
}

export function ConfigRow({ row, ctx }: { row: Row; ctx: PaneCtx }) {
  const meta = fieldMeta(row.field);
  const own = row.source === "chain" || row.source === undefined;
  const [editing, setEditing] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const readOnly = useContext(ReadOnly);
  const label = meta.label;
  // `on_base_changed` is edited by its one key (the server writes the mapping).
  const target = row.field === "on_base_changed" ? "on_base_changed.restart_from" : row.field;
  const save = (text: string) => {
    if (editing === null) return;
    // Nothing typed: no op, so no undo step and no override of an equal value.
    if (text === editing) return void (setEditing(null), setErr(null));
    const p = parse(text, meta.kind);
    if ("error" in p) return setErr(`${label}: ${p.error}`);
    setEditing(null);
    setErr(null);
    ctx.draft.field(ctx.path, target, p.value);
  };
  const start = () => {
    const v = row.field === "on_base_changed" ? (row.value as { restart_from?: string } | null)?.restart_from : row.value;
    setEditing(Array.isArray(v) ? v.join(", ") : v == null ? "" : String(v));
  };
  return (
    <div className={`cfg-row${own ? " is-own" : ""}`}>
      <span className="cfg-k">{label}</span>
      {meta.kind === "bool" && !readOnly ? (
        // A yes/no field is a switch: one click sets it.
        <span className="cfg-v"><Switch label={label} checked={!!row.value} disabled={!!row.locked} onChange={(on) => ctx.draft.field(ctx.path, row.field, on)} /></span>
      ) : editing === null ? (
        <span className={`cfg-v${meta.kind === "long" ? " is-prose" : ""}${row.value == null || row.value === "" ? " is-unset" : ""}`}>{show(row.value, meta.kind)}</span>
      ) : (
        <Editor meta={meta} field={row.field} value={editing} ctx={ctx} onSave={save} onRefuse={(m) => setErr(`${label}: ${m}`)} onCancel={() => { setEditing(null); setErr(null); }} label={label} />
      )}
      {own && row.source !== undefined && <span className="cfg-dot" aria-label="overridden here" />}
      {row.source !== undefined && <span className={`cfg-chip${own ? " is-own" : ""}`} title={row.source}>{sourceWord(row.source)}</span>}
      {readOnly ? null : row.locked ? (
        <span className="cfg-lock" title={row.locked}>locked</span>
      ) : meta.elsewhere ? (
        <>
          <span className="cfg-lock" title={meta.elsewhere}>on Overview</span>
          {own && row.source !== undefined ? <IconButton label={`Reset ${label}`} onClick={() => ctx.draft.ops([{ op: "reset_field", path: ctx.path, field: row.field }])}><RotateCcw size={12} aria-hidden /></IconButton> : <span className="cfg-gap" />}
        </>
      ) : (
        editing === null && (
          <>
            {meta.kind === "bool" ? <span className="cfg-gap" /> : <IconButton label={`Edit ${label}`} onClick={start}><Pencil size={12} aria-hidden /></IconButton>}
            {own ? <IconButton label={`Reset ${label}`} onClick={() => ctx.draft.ops([{ op: "reset_field", path: ctx.path, field: row.field }])}><RotateCcw size={12} aria-hidden /></IconButton> : <span className="cfg-gap" />}
          </>
        )
      )}
      {row.recovery && <p className="cfg-note">applies to its on-failure recovery</p>}
      {err && <p className="cfg-err" role="alert">{err}</p>}
    </div>
  );
}

function Editor({ meta, field, value, label, ctx, onSave, onRefuse, onCancel }: { meta: FieldMeta; field: string; value: string; label: string; ctx: PaneCtx; onSave: (t: string) => void; onRefuse: (message: string) => void; onCancel: () => void }) {
  const [text, setText] = useState(value);
  const opts = useHarnessOptions();
  const library = useLibrary();
  const listed = useProviders();
  const harness = String(ctx.r.sources[ctx.path]?.harness?.value ?? "claude");
  const options = meta.options === "harness"
    ? typeof opts === "string" ? null : opts.harnesses.profiles.map((p) => p.id)
    : meta.options === "profile"
      ? typeof opts === "string" ? null : opts.harnesses.agent_profiles.map((p) => p.id)
      : meta.options === "effort"
        ? effortsFor(opts, harness)
        : meta.options === "restart"
          ? restartTargets(ctx)
          : meta.options ?? null;
  const choices = choicesFor(meta, ctx, typeof opts === "string" ? null : opts.harnesses, library);
  const suggested = meta.suggest === "model" ? modelsFor(listed, typeof opts === "string" ? null : opts.harnesses, harness) : null;
  const multiple = meta.kind === "list";
  // A closed set saves only listed values; the rest are refused where they were typed.
  const save = (t: string) => {
    const bad = choices?.length ? unlisted(t, choices, multiple) : [];
    if (bad.length) return onRefuse(notListed(bad, NOUN[meta.choices!])!);
    onSave(t);
  };
  const keys = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.stopPropagation(); onCancel(); }
    if (e.key === "Enter" && !(meta.kind === "long" && e.shiftKey)) { e.preventDefault(); save(text); }
  };
  if (options && options.length)
    return (
      <select autoFocus aria-label={label} className="cfg-edit" value={text} onKeyDown={keys} onBlur={() => onSave(text)} onChange={(e) => { setText(e.target.value); onSave(e.target.value); }}>
        <option value="">not set</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  if (meta.kind === "long") return <textarea autoFocus aria-label={label} className="cfg-edit is-long" rows={4} value={text} onKeyDown={keys} onBlur={() => onSave(text)} onChange={(e) => setText(e.target.value)} />;
  if (choices?.length || suggested?.length)
    return (
      <Combobox
        autoFocus
        aria-label={label}
        className="cfg-edit"
        value={text}
        choices={choices?.length ? choices : suggested!}
        closed={!!choices?.length}
        multiple={multiple}
        noun={meta.choices ? NOUN[meta.choices] : meta.label}
        listLabel={label}
        onChange={setText}
        // One value picked is the edit; a list waits for Enter, so more can be added.
        onPick={multiple ? undefined : save}
        onKeyDown={keys}
        onBlur={() => save(text)}
      />
    );
  return <input autoFocus aria-label={label} className="cfg-edit" value={text} spellCheck={false} placeholder={field.endsWith("_minutes") ? "minutes" : undefined} onKeyDown={keys} onBlur={() => onSave(text)} onChange={(e) => setText(e.target.value)} />;
}

/** What one value of each closed set is, for its refusal. */
const NOUN: Record<ChoiceSource, string> = {
  ref: "action",
  target: "target",
  inputs: "input",
  grants: "grant",
  steering: "steering profile",
  harnesses: "harness",
  documents: "document produced before this gate",
  earlier: "exec node before this gate",
};

/** The closed set a typed field takes its values from, or null when it has none. */
function choicesFor(meta: FieldMeta, ctx: PaneCtx, h: Harnesses | null, library: ReturnType<typeof useLibrary>): Choice[] | null {
  const plain = (vs: string[]) => vs.map((value) => ({ value }));
  switch (meta.choices) {
    case "ref":
    case "target":
    case "inputs":
    case "grants":
      return ctx.r.choices?.[meta.choices] ?? null;
    case "steering":
      return steeringChoices(library);
    case "harnesses":
      return harnessChoices(h);
    case "documents":
      return plain(ctx.r.resolved?.documents[ctx.path] ?? []);
    case "earlier":
      return plain(earlierExec(ctx));
    default:
      return null;
  }
}

/** The models a harness's provider is known to take, to suggest (any other may still be typed). */
const modelsFor = (listed: ProviderStatus[], h: Harnesses | null, harness: string): Choice[] => {
  const provider = h?.profiles.find((p) => p.id === harness)?.provider ?? harness;
  return (listed.find((p) => p.id === provider)?.models ?? []).map((value) => ({ value }));
};
