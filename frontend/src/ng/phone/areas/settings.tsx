import { detailOf } from "../../http";
import { useProviders } from "../../harnesses/useProviders";
import type { ConfigDraft } from "../../templates/draft/useConfigDraft";
import { rowChanged, sourceWord } from "../../templates/draft/view";
import { choicesFor, modelsFor, noRestart, NOUN, optionsFor, pickerOptions } from "../../templates/fieldChoices";
import { editedValue, editOps, fieldMeta, parse, RECOVERY_NOTE, show } from "../../templates/fields";
import { useHarnessOptions } from "../../templates/panes/useHarnessOptions";
import { useLibrary } from "../../templates/useLibrary";
import type { Edit, RowSpec } from "./kit";

/** A setting as the desktop's Config tab lists it (`settingRows`, `chainCapGroups`): a field, what it is now and where that comes from. */
export type SettingRow = { field: string; value: unknown; source?: string; locked?: string; recovery?: boolean };

const unset = (x: SettingRow) => (x.value == null || x.value === "" || (Array.isArray(x.value) && !x.value.length)) && x.source !== "chain" && !x.locked && !x.recovery;

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
    /** `field` is the row's; `current`, its value now, says whether a merge rejection is added first. */
    const send = async (field: string, value: unknown, current?: unknown) => {
      const a = await draft.ops(editOps(path, field, value, current), { quiet: true });
      return a.status === 200 ? null : detailOf(a.body);
    };
    const build = (x: SettingRow): RowSpec => {
      const meta = fieldMeta(x.field);
      const label = meta.label;
      const own = x.source === "chain" || x.source === undefined;
      // A policy key is reported by the group (`fields: ["policy"]`), so one this node sets stands for it.
      const changed = rowChanged(r, path, x.field, x.source);
      const base: RowSpec = { key: x.field, label, mono: true, changed, sub: x.locked ?? (x.recovery ? RECOVERY_NOTE : x.source === undefined ? undefined : own ? "set here" : `from ${sourceWord(x.source)}`), value: show(x.value, meta.kind, meta.labels) };
      if (readOnly || x.locked) return base;
      if (meta.elsewhere) return { ...base, sub: meta.elsewhere };
      // A merge rejection with no node to restart from only reads: anything typed would be refused.
      const stuck = noRestart(meta, at);
      if (stuck) return { ...base, sub: stuck };
      if (meta.kind === "bool") return { ...base, value: undefined, sw: !!x.value, onSwitch: (on) => void send(x.field, on) };
      // A node policy is edited by its one key; a merge rejection is added whole the first time (`editOps`).
      const current = editedValue(x.field, x.value);
      const list = optionsFor(meta, at, opts);
      if (list?.length)
        return { ...base, onEdit: () => edit({ kind: "choice", title: label, options: pickerOptions(meta, list, x.field, x.value), value: typeof current === "string" ? current : "", set: (v) => send(x.field, v || null, x.value) }) };
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
            return "error" in p ? Promise.resolve(p.error) : send(x.field, p.value, x.value);
          },
        }),
      };
    };
    return { set: rows.filter((x) => !unset(x)).map(build), unset: rows.filter(unset).map(build) };
  };
}
