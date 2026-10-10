import { X } from "lucide-react";
import { IconButton } from "../../ui/IconButton";
import { useContext } from "react";
import { ReadOnly } from "../plugin";
import { Kv, type Option } from "./controls";

/** One `fallback:` entry (`FallbackEntry`): an optional harness plus one route,
 *  a profile or a model and/or effort. Whatever it omits is the task's own. */
export type Entry = { harness?: string; profile?: string; model?: string; effort?: string };

/** The entries a `fallback` value holds, without the keys a resolved one
 *  carries as null, so writing the list back writes only what is set. A bare
 *  name, which the schema refuses, reads as the harness it meant, so writing
 *  the list back repairs it. */
export function entriesOf(v: unknown): Entry[] {
  if (!Array.isArray(v)) return [];
  return v.flatMap((e): Entry[] =>
    typeof e === "string" ? [{ harness: e }] : e && typeof e === "object" ? [Object.fromEntries(Object.entries(e).filter(([, x]) => x != null)) as Entry] : [],
  );
}

/** An entry as one line: `codex · strong`, `cursor · model auto`. */
export const entryText = (e: Entry) =>
  [e.harness ?? "task's harness", e.profile, e.model && `model ${e.model}`, e.effort && `effort ${e.effort}`].filter(Boolean).join(" · ");

const drop = <K extends keyof Entry>(e: Entry, ...keys: K[]): Entry => {
  const out = { ...e };
  for (const k of keys) delete out[k];
  return out;
};

/** An agent task's `fallback:` list (`fallback-is-opt-in`): one row per entry,
 *  its harness and profile picked from the harness profiles, tried in order.
 *  A model or effort an entry sets is shown and kept, and edited in YAML.
 *  Each change sends the whole list; removing the last entry unsets it, so an
 *  agent profile's own list applies again. */
export function FallbackRows({ value, harness, harnesses, profiles, onChange }: {
  value: unknown;
  /** The task's own harness: a new entry tries another first. */
  harness: string;
  /** The harness profiles; null while they load. */
  harnesses: string[] | null;
  profiles: string[] | null;
  onChange: (next: Entry[] | null) => void;
}) {
  const entries = entriesOf(value);
  const readOnly = useContext(ReadOnly);
  if (readOnly) return <Kv k="fallback" v={entries.map(entryText).join(" → ") || "none"} muted={!entries.length} />;
  const set = (i: number, e: Entry) => onChange(entries.map((x, j) => (j === i ? e : x)));
  const add = () => onChange([...entries, { harness: harnesses?.find((h) => h !== harness && !entries.some((e) => e.harness === h)) ?? harnesses?.[0] ?? harness }]);
  const remove = (i: number) => {
    const next = entries.filter((_, j) => j !== i);
    onChange(next.length ? next : null);
  };
  return (
    <div className="tpl-pf" role="group" aria-label="fallback">
      <span className="tpl-pf-label">fallback</span>
      {entries.length > 0 && (
        <ol className="fb-list">
          {entries.map((e, i) => {
            const n = i + 1;
            const own = !!(e.model || e.effort);
            // An entry names something: a harness, a profile, or a model or effort.
            const harnessOpts: Option[] = [
              ...(e.harness === undefined || e.profile || own ? [{ value: "", label: "task's harness" }] : []),
              ...(harnesses ?? []).map((h) => ({ value: h, label: h })),
              ...(e.harness && !harnesses?.includes(e.harness) ? [{ value: e.harness, label: `${e.harness} (not a harness)` }] : []),
            ];
            const routeOpts: Option[] = [
              ...(e.harness || own || e.profile === undefined ? [{ value: "", label: own ? `model ${[e.model, e.effort && `effort ${e.effort}`].filter(Boolean).join(" · ")}` : "task's route" }] : []),
              ...(profiles ?? []).map((p) => ({ value: p, label: p })),
              ...(e.profile && !profiles?.includes(e.profile) ? [{ value: e.profile, label: `${e.profile} (not a profile)` }] : []),
            ];
            return (
              <li key={i} className="fb-row">
                <span className="fb-n" aria-hidden>{n}</span>
                <select aria-label={`Fallback ${n} harness`} className="tpl-pf-select" value={e.harness ?? ""} onChange={(x) => set(i, x.target.value ? { ...e, harness: x.target.value } : drop(e, "harness"))}>
                  {harnessOpts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                <select aria-label={`Fallback ${n} profile`} className="tpl-pf-select" value={e.profile ?? ""} onChange={(x) => set(i, x.target.value ? { ...drop(e, "model", "effort"), profile: x.target.value } : drop(e, "profile"))}>
                  {routeOpts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                <IconButton label={`Remove fallback ${n}`} onClick={() => remove(i)}><X size={12} aria-hidden /></IconButton>
              </li>
            );
          })}
        </ol>
      )}
      <p className="tpl-pf-sub">
        {entries.length ? "Tried in order when a launch is rate-limited or its harness unavailable." : Array.isArray(value) ? "None: a launch is never retried elsewhere." : "None. An agent profile's own list applies."} A model or effort is set in YAML.
      </p>
      <button type="button" className="fb-add" onClick={add} disabled={!harnesses}>+ Add a fallback</button>
    </div>
  );
}
