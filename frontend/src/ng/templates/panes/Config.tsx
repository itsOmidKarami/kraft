import { useState } from "react";
import { Pencil, RotateCcw } from "lucide-react";
import { IconButton } from "../../ui/IconButton";
import { authoredAt, authoredNodes, kindOf, resolvedAt, sourceRows, sourceWord } from "../draft/view";
import { fieldMeta, parse, show, type FieldMeta } from "../fields";
import { Head, Note } from "./controls";
import type { PaneKind } from "./describe";
import type { PaneCtx } from "./Overview";
import { effortsFor, useHarnessOptions } from "./useHarnessOptions";

/** A row's `source` is absent where there is none to name (a library component's own keys): no chip, no dot, and ↺ removes the key. */
export type Row = { field: string; value: unknown; source?: string; locked?: string };

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
  if (kind === "chain") return <ChainConfig ctx={ctx} />;
  const rows: Row[] = sourceRows(r, path);
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
  const label = meta.label;
  // `on_base_changed` is edited by its one key (the server writes the mapping).
  const target = row.field === "on_base_changed" ? "on_base_changed.restart_from" : row.field;
  const save = (text: string) => {
    if (editing === null) return;
    // Nothing typed: no op, so no undo step and no override of an equal value.
    if (text === editing) return void setEditing(null);
    const p = parse(text, meta.kind);
    if ("error" in p) return setErr(`${label}: ${p.error}`);
    setEditing(null);
    setErr(null);
    ctx.draft.field(ctx.path, target, p.value);
  };
  const start = () => {
    if (meta.kind === "bool") return ctx.draft.field(ctx.path, row.field, !row.value);
    const v = row.field === "on_base_changed" ? (row.value as { restart_from?: string } | null)?.restart_from : row.value;
    setEditing(Array.isArray(v) ? v.join(", ") : v == null ? "" : String(v));
  };
  return (
    <div className={`cfg-row${own ? " is-own" : ""}`}>
      <span className="cfg-k">{label}</span>
      {editing === null ? (
        <span className={`cfg-v${meta.kind === "long" ? " is-prose" : ""}${row.value == null || row.value === "" ? " is-unset" : ""}`}>{show(row.value, meta.kind)}</span>
      ) : (
        <Editor meta={meta} field={row.field} value={editing} ctx={ctx} onSave={save} onCancel={() => { setEditing(null); setErr(null); }} label={label} />
      )}
      {own && row.source !== undefined && <span className="cfg-dot" aria-label="overridden here" />}
      {row.source !== undefined && <span className={`cfg-chip${own ? " is-own" : ""}`} title={row.source}>{sourceWord(row.source)}</span>}
      {row.locked ? (
        <span className="cfg-lock" title={row.locked}>locked</span>
      ) : (
        editing === null && (
          <>
            <IconButton label={meta.kind === "bool" ? `Turn ${label} ${row.value ? "off" : "on"}` : `Edit ${label}`} onClick={start}><Pencil size={12} aria-hidden /></IconButton>
            {own ? <IconButton label={`Reset ${label}`} onClick={() => ctx.draft.ops([{ op: "reset_field", path: ctx.path, field: row.field }])}><RotateCcw size={12} aria-hidden /></IconButton> : <span className="cfg-gap" />}
          </>
        )
      )}
      {err && <p className="cfg-err" role="alert">{err}</p>}
    </div>
  );
}

function Editor({ meta, field, value, label, ctx, onSave, onCancel }: { meta: FieldMeta; field: string; value: string; label: string; ctx: PaneCtx; onSave: (t: string) => void; onCancel: () => void }) {
  const [text, setText] = useState(value);
  const opts = useHarnessOptions();
  const options = meta.options === "harness"
    ? typeof opts === "string" ? null : opts.harnesses.profiles.map((p) => p.id)
    : meta.options === "profile"
      ? typeof opts === "string" ? null : opts.harnesses.agent_profiles.map((p) => p.id)
      : meta.options === "effort"
        ? effortsFor(opts, String(ctx.r.sources[ctx.path]?.harness?.value ?? "claude"))
        : meta.options === "restart"
          ? restartTargets(ctx)
          : meta.options ?? null;
  const keys = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.stopPropagation(); onCancel(); }
    if (e.key === "Enter" && !(meta.kind === "long" && e.shiftKey)) { e.preventDefault(); onSave(text); }
  };
  if (options && options.length)
    return (
      <select autoFocus aria-label={label} className="cfg-edit" value={text} onKeyDown={keys} onBlur={() => onSave(text)} onChange={(e) => { setText(e.target.value); onSave(e.target.value); }}>
        <option value="">not set</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  if (meta.kind === "long") return <textarea autoFocus aria-label={label} className="cfg-edit is-long" rows={4} value={text} onKeyDown={keys} onBlur={() => onSave(text)} onChange={(e) => setText(e.target.value)} />;
  return <input autoFocus aria-label={label} className="cfg-edit" value={text} spellCheck={false} placeholder={field.endsWith("_minutes") ? "minutes" : undefined} onKeyDown={keys} onBlur={() => onSave(text)} onChange={(e) => setText(e.target.value)} />;
}
