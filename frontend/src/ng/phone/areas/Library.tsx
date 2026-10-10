import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { SECTION_LABEL, SECTIONS, parseRef, pluginLabel, refUrl, type PublishedLibrary, type Section } from "../../library/types";
import { listRows } from "../../library/rows";
import { detailOf, request } from "../../http";
import { useConfigDraft } from "../../templates/draft/useConfigDraft";
import { authoredAt } from "../../templates/draft/view";
import { AreaScreen } from "./AreaScreen";
import { Group, useEditor, type RowSpec } from "./kit";
import { TabStrip } from "../ui/Rows";

/** The published library, for who uses what (`GET /templates/library`); read once per screen. */
function usePublished() {
  const [lib, setLib] = useState<PublishedLibrary | null>(null);
  useEffect(() => {
    let live = true;
    void request<PublishedLibrary>("/templates/library").then((r) => live && r.status === 200 && Array.isArray(r.body.components) && setLib(r.body));
    return () => void (live = false);
  }, []);
  return lib;
}

const isMap = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const NOT_SETTINGS = new Set(["id", "kind", "icon", "extends", "steps", "tasks", "on_failure", "fix_loop", "escalation", "auto_review", "judge", "on_conflict", "instructions"]);
/** A component's own settings, each as its dotted field (the desktop's `ownRows`, pinned to it by Library.test). */
export function ownRows(own: Record<string, unknown> | null): { field: string; value: unknown }[] {
  const rows: { field: string; value: unknown }[] = [];
  const walk = (o: Record<string, unknown>, prefix: string) => {
    for (const [k, v] of Object.entries(o)) {
      if (!prefix && NOT_SETTINGS.has(k)) continue;
      const field = `${prefix}${k}`;
      if (isMap(v) && k !== "on_base_changed") walk(v, `${field}.`);
      else rows.push({ field, value: v });
    }
  };
  if (own) walk(own, "");
  return rows;
}

const show = (v: unknown) => (v == null ? "not set" : Array.isArray(v) ? v.map(String).join(", ") : String(v));

/** `/templates/library`: Nodes · Steps · Tasks · Steering (Steps added, GAP §5.1), a filter, and one row per component (W17 brief L.2). */
export function LibraryList() {
  const draft = useConfigDraft("library", "library");
  const published = usePublished();
  const [params, setParams] = useSearchParams();
  const kind = (SECTIONS as readonly string[]).includes(params.get("kind") ?? "") ? (params.get("kind") as Section) : "nodes";
  const q = params.get("q") ?? "";
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k));
    setParams(next, { replace: true });
  };
  const rows = useMemo(() => (draft.view ? listRows(draft.view.result, published?.components ?? null, draft.view.plugin_library) : []).filter((r) => r.section === kind && (!q.trim() || r.name.toLowerCase().includes(q.trim().toLowerCase()))), [draft.view, published, kind, q]);
  return (
    <AreaScreen title="Library" sub="Nodes, steps, tasks and steering that chains and repos reuse." draft={draft}>
      <label className="ph-search-field ph-area-search">
        <input type="search" className="ph-search-input" aria-label="Search the library" placeholder="Search the library" value={q} onChange={(e) => set({ q: e.target.value })} />
      </label>
      <TabStrip label="Library" value={kind} onChange={(k) => set({ kind: k === "nodes" ? "" : k })} tabs={SECTIONS.map((s) => ({ id: s, label: SECTION_LABEL[s] }))} />
      {draft.status === "error" && <p className="ph-error" role="alert">The library could not be read.</p>}
      {draft.view && rows.length === 0 && <p className="ph-empty">{q ? "Nothing matches." : `No ${SECTION_LABEL[kind].toLowerCase()} yet.`}</p>}
      <Group
        rows={rows.map((r): RowSpec => ({ key: r.id, label: r.name, mono: true, sub: r.used ? `used in ${r.used}` : undefined, to: refUrl(r.id), icon: undefined, chips: r.plugin ? [{ label: pluginLabel(r.plugin) }] : r.problem ? [{ label: "problem", tone: "bad" }] : r.mark ? [{ label: r.mark === "add" ? "new" : "changed", tone: "warn" }] : undefined }))}
      />
    </AreaScreen>
  );
}

/** `/templates/library/:ref`: one component's rows. Its scalar settings are editable through the same `set_field` op the desktop sends, a steering profile's instructions too; the rest is read-only. */
export function LibraryComponentView() {
  const { ref } = useParams();
  const parsed = parseRef(ref);
  const draft = useConfigDraft("library", "library");
  const published = usePublished();
  const { edit, node: sheet } = useEditor();
  const path = ref ?? "";
  // A plugin's component is not in the draft: it is read from the plugins' library, and nothing here edits it.
  const shipped = (parsed && draft.view?.plugin_library?.[parsed.section]?.[parsed.name]) || null;
  const own = shipped ?? (draft.view && parsed ? authoredAt(draft.view.result, draft.scope, path) : null);
  const send = async (op: Record<string, unknown>) => {
    const a = await draft.ops([op as never], { quiet: true });
    return a.status === 200 ? null : detailOf(a.body);
  };
  const setField = (field: string, value: unknown) => send({ op: "set_field", path, field, value });
  const changed = (field: string) => !!draft.view?.result.changes.some((c) => c.path === path && (c.fields ?? []).includes(field));

  const settings: RowSpec[] = ownRows(own).map(({ field, value }): RowSpec => {
    const base: RowSpec = { key: field, label: field, mono: true, changed: changed(field) };
    if (shipped) return { ...base, value: show(value) };
    if (typeof value === "boolean") return { ...base, sw: value, onSwitch: (on) => void setField(field, on) };
    // An action or a target is one of the values the draft lists, not free text.
    const choices = field === "ref" || field === "target" ? draft.view?.result.choices?.[field] ?? [] : [];
    const listed = choices.length ? { choices, closed: true, noun: field === "ref" ? "action" : "target" } : undefined;
    if (typeof value === "string" || typeof value === "number")
      return { ...base, value: show(value), onEdit: () => edit({ kind: "text", title: field, value: String(value), listed, set: (v) => (typeof value === "number" ? (v.trim() && Number.isFinite(Number(v)) ? setField(field, Number(v)) : Promise.resolve("Enter a number.")) : setField(field, v)) }) };
    return { ...base, value: show(value) };
  });
  const instructions = typeof own?.instructions === "string" ? own.instructions : null;
  const pub = published?.components.find((c) => c.id === path);
  const uses = pub?.used_by_paths ?? [];
  const problems = (draft.view?.result.problems ?? []).filter((p) => p.path === path || p.path.startsWith(`${path}.`) || p.component === path);

  return (
    <AreaScreen title={parsed?.name ?? "Library"} sub={parsed ? `${SECTION_LABEL[parsed.section].replace(/s$/, "")}${pub?.plugin ? ` · ${pluginLabel(pub.plugin)}` : ""}` : undefined} draft={draft}>
      {draft.view && !own && <p className="ph-empty">There is no component {path}.</p>}
      {problems.length > 0 && <Group title="Problems" rows={problems.map((p, i): RowSpec => ({ key: `p${i}`, label: p.message.replace(/^Value error, /, ""), sub: [p.chain && `breaks ${p.chain}`, p.repo && `repo ${p.repo}`].filter(Boolean).join(" · ") || undefined }))} />}
      {own && <Group rows={[{ label: "kind", value: typeof own.kind === "string" ? own.kind : parsed?.section ?? "", mono: true }, ...(typeof own.extends === "string" ? [{ label: "extends", value: own.extends, mono: true } as RowSpec] : []), ...settings]} />}
      {instructions !== null && (
        <Group title="Instructions" rows={[{ label: instructions.trim() ? instructions : "Required.", mono: false, onEdit: shipped ? undefined : () => edit({ kind: "text", title: "Instructions", value: instructions, set: (v) => (v.trim() ? setField("instructions", v) : Promise.resolve("Instructions can't be empty.")) }) }]} note="Added to what an agent task reads at launch, after the repository's own steering." />
      )}
      {parsed && <Group title="Used in" rows={uses.length ? uses.map((u, i): RowSpec => ({ key: `${u.chain}:${u.path}:${i}`, label: u.chain, mono: true, sub: `${u.path}${u.via ? ` via ${u.via}` : ""}`, to: `/templates/chains/${encodeURIComponent(u.chain)}` })) : [{ label: published ? "Not used by any chain." : "Reading…" }]} />}
      {sheet}
    </AreaScreen>
  );
}
