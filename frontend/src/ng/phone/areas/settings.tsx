import { detailOf } from "../../http";
import { useProviders } from "../../harnesses/useProviders";
import type { ConfigDraft } from "../../templates/draft/useConfigDraft";
import { sourceWord } from "../../templates/draft/view";
import { choicesFor, modelsFor, NOUN, optionsFor } from "../../templates/fieldChoices";
import { fieldMeta, parse, show } from "../../templates/fields";
import { useHarnessOptions } from "../../templates/panes/useHarnessOptions";
import { useLibrary } from "../../templates/useLibrary";
import type { Edit, RowSpec } from "./kit";

/** A setting as the desktop's Config tab lists it (`settingRows`, `chainCapGroups`): a field, what it is now and where that comes from. */
export type SettingRow = { field: string; value: unknown; source?: string; locked?: string };

const unset = (x: SettingRow) => (x.value == null || x.value === "" || (Array.isArray(x.value) && !x.value.length)) && x.source !== "chain" && !x.locked;

/** A setting row's edit, the desktop's `ConfigRow` on a sheet: the same labels, closed sets, parse and `set_field` op. `readOnly` shows the values and edits none (an add-on's chain). */
export function useSettingRows(draft: ConfigDraft, edit: (e: Edit) => void, readOnly: boolean) {
  const opts = useHarnessOptions();
  const library = useLibrary();
  const providers = useProviders();
  return (path: string, rows: SettingRow[]): { set: RowSpec[]; unset: RowSpec[] } => {
    const r = draft.view!.result;
    const at = { r, scope: draft.scope, path };
    const harness = String(r.sources[path]?.harness?.value ?? "claude");
    const harnesses = typeof opts === "string" ? null : opts.harnesses;
    const send = async (field: string, value: unknown) => {
      const a = await draft.ops([{ op: "set_field", path, field, value } as never], { quiet: true });
      return a.status === 200 ? null : detailOf(a.body);
    };
    const build = (x: SettingRow): RowSpec => {
      const meta = fieldMeta(x.field);
      const label = meta.label;
      const own = x.source === "chain" || x.source === undefined;
      const changed = r.changes.some((c) => c.path === path && (c.fields ?? []).includes(x.field));
      const base: RowSpec = { key: x.field, label, mono: true, changed, sub: x.locked ?? (x.source === undefined ? undefined : own ? "set here" : `from ${sourceWord(x.source)}`), value: show(x.value, meta.kind) };
      if (readOnly || x.locked) return base;
      if (meta.elsewhere) return { ...base, sub: "Edited on desktop." };
      if (meta.kind === "bool") return { ...base, value: undefined, sw: !!x.value, onSwitch: (on) => void send(x.field, on) };
      // `on_base_changed` is edited by its one key (the server writes the mapping).
      const field = x.field === "on_base_changed" ? "on_base_changed.restart_from" : x.field;
      const current = x.field === "on_base_changed" ? (x.value as { restart_from?: string } | null)?.restart_from : x.value;
      const list = optionsFor(meta, at, opts);
      if (list?.length)
        return { ...base, onEdit: () => edit({ kind: "choice", title: label, options: [{ value: "", label: "not set" }, ...list.map((value) => ({ value, label: value }))], value: typeof current === "string" ? current : "", set: (v) => send(field, v || null) }) };
      const choices = choicesFor(meta, at, harnesses, library);
      const closed = !!choices?.length;
      const suggested = meta.suggest === "model" ? modelsFor(providers, harnesses, harness) : [];
      const multiple = meta.kind === "list";
      const listed = closed || suggested.length ? { choices: closed ? choices! : suggested, closed, multiple, noun: meta.choices ? NOUN[meta.choices] : label } : undefined;
      const start = Array.isArray(current) ? current.join(", ") : current == null ? "" : String(current);
      return {
        ...base,
        onEdit: () => edit({
          kind: "text", title: label, value: start, multiline: meta.kind === "long", listed,
          set: (t) => {
            if (t.trim() === start.trim()) return Promise.resolve(null);
            const p = parse(t, meta.kind);
            return "error" in p ? Promise.resolve(p.error) : send(field, p.value);
          },
        }),
      };
    };
    return { set: rows.filter((x) => !unset(x)).map(build), unset: rows.filter(unset).map(build) };
  };
}
