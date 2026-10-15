import { useState, type ReactNode } from "react";
import { Segmented } from "../../ui/Segmented";
import { detailOf } from "../../http";
import type { ConfigDraft } from "../draft/useConfigDraft";
import type { Problem, Result } from "../draft/types";
import { ValueCell } from "../draft/ValueCell";
import { harnessChoices, steeringChoices } from "../choices";
import { useHarnessOptions } from "../panes/useHarnessOptions";
import { useLibrary } from "../useLibrary";
import { FIELDS, forgeOptions, GROUPS, YAML_ONLY, patchFor, sourceOf, valueOf, type RepoField } from "./fields";
import { Fold, Head } from "../panes/controls";
import { problemsOf, type RepoView } from "./types";

const key = (p: Problem) => `${p.repo}|${p.field}|${p.message}`;

/** Sends `set_repo`, checked first with ?preview=1 (Decided 8): a problem the edit
 *  would add at this repo answers its message and nothing is saved. */
export async function setRepo(draft: ConfigDraft, repo: RepoView, patch: Record<string, unknown>): Promise<string | null> {
  const op = { op: "set_repo", path: repo.path, patch };
  const before = new Set(problemsOf(draft.view!.result, repo.path).map(key));
  const check = await draft.ops([op], { preview: true });
  if (check.status !== 200) return detailOf(check.body);
  const fresh = problemsOf((check.body as unknown as { result: Result }).result, repo.path).filter((p) => !before.has(key(p)));
  if (fresh.length) return `Refused: ${fresh[0].message}`;
  const done = await draft.ops([op], { quiet: true });
  return done.status === 200 ? null : detailOf(done.body);
}

/** Every Config row of a repo (GAP §2 #33), each with its source word and a
 *  Reset on a value this repo sets, under the section heads of `GROUPS`. A row
 *  still at its default folds into one line per group unless it has a problem
 *  or a change. The keys with no row say they are edited in YAML. */
export function RepoConfig({ draft, repo, chains, onYaml }: { draft: ConfigDraft; repo: RepoView; chains: string[]; onYaml: () => void }) {
  const r = draft.view!.result;
  const own = problemsOf(r, repo.path);
  const diff = r.changes.find((c) => c.path === repo.path)?.fields ?? [];
  const row = (f: RepoField) => {
    const problem = own.find((p) => p.field === f.key || (f.key.startsWith("policy.") && p.field === f.key.slice(7)));
    const changed = diff.includes(f.key.split(".")[0]);
    return { f, changed, problem, folded: !isSet(repo, f) && !problem && !changed };
  };
  const draw = ({ f, changed, problem }: ReturnType<typeof row>) => <Row key={f.key} draft={draft} repo={repo} f={f} chains={chains} problem={problem} changed={changed} />;
  return (
    <div className="rp-cfg">
      {GROUPS.map((g) => {
        const rows = FIELDS.filter((f) => f.group === g).map(row);
        return (
          <section key={g} aria-label={g} className="rp-group">
            <Head>{g}</Head>
            {rows.filter((x) => !x.folded).map(draw)}
            <Fold count={rows.filter((x) => x.folded).length}>{rows.filter((x) => x.folded).map(draw)}</Fold>
          </section>
        );
      })}
      <section aria-label="Edited in YAML" className="rp-group">
        <Head>Edited in YAML</Head>
        {YAML_ONLY.map(({ key, label }) => (
          <div key={key} className="rp-row-cfg">
            <span className="rp-k">{label}</span>
            <span className="rp-v">{yamlValue(repo.entry[key])}</span>
            <span className="rp-src"><button type="button" className="rp-reset" aria-label={`Edit ${label} in YAML`} onClick={onYaml}>Edit in YAML</button></span>
          </div>
        ))}
      </section>
    </div>
  );
}

/** A key the Config tab has no row for, as its entry holds it. */
function yamlValue(v: unknown): ReactNode {
  if (v === undefined || v === null || (typeof v === "object" && !Object.keys(v).length)) return <span className="rp-yaml-none">not set</span>;
  return <span className="rp-yaml-val">{typeof v === "object" ? JSON.stringify(v) : String(v)}</span>;
}

/** The entry sets the field (an empty list or map counts as unset, an empty string as set). */
const isSet = (repo: RepoView, f: RepoField) => {
  const v = valueOf(repo, f);
  return v != null && !(Array.isArray(v) && v.length === 0) && !(typeof v === "object" && !Array.isArray(v) && !Object.keys(v).length);
};

function Row({ draft, repo, f, chains, problem, changed }: { draft: ConfigDraft; repo: RepoView; f: RepoField; chains: string[]; problem?: Problem; changed: boolean }) {
  const v = valueOf(repo, f);
  const shown = v === "" && f.empty ? f.empty : f.show(f.key === "steering" ? repo.resolved.steering : v);
  const set = isSet(repo, f);
  const source = sourceOf(repo, f);
  const library = useLibrary();
  const opts = useHarnessOptions();
  const choices = f.choices === "steering" ? steeringChoices(library) : f.choices === "harnesses" ? harnessChoices(typeof opts === "string" ? null : opts.harnesses) : undefined;
  const [refused, setRefused] = useState<string | null>(null);
  /** A refusal of the `none` checkbox, which has no cell of its own to show one. */
  const [noneRefused, setNoneRefused] = useState<string | null>(null);
  const send = async (value: unknown) => {
    const msg = await setRepo(draft, repo, patchFor(repo, f, value));
    setRefused(msg);
    return msg;
  };
  const commit = (t: string) => {
    const p = f.parse(t);
    if ("error" in p) return p.error;
    return send(p.value);
  };

  return (
    <div className="rp-row-cfg">
      <span className="rp-k">{f.label}</span>
      <span className="rp-v">
        {f.choice === "forge" ? (
          <Segmented label="forge" value={(typeof v === "string" ? v : "none")} options={forgeOptions(v).map((x) => ({ value: x, label: x }))} onChange={(x) => void send(x === "none" ? null : x)} />
        ) : f.choice === "chain" ? (
          <select className="rp-select" aria-label={f.label} value={String(v ?? f.fallback ?? "default")} onChange={(e) => void send(e.target.value)}>
            {[...new Set([String(v ?? "default"), ...chains])].map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        ) : (
          <ValueCell label={f.label} value={f.show(v)} display={shown || f.fallback || "not set"} muted={!shown} bad={!!problem} changed={changed} placeholder={f.placeholder} choices={choices} closed multiple noun={f.choices === "steering" ? "steering profile" : "harness"} onCommit={commit} />
        )}
        {f.none && (
          <label className="rp-none">
            <input type="checkbox" checked={v === ""} onChange={(e) => void send(e.target.checked ? "" : null).then(setNoneRefused)} /> {f.none}
          </label>
        )}
        {(problem || (f.choice && refused) || noneRefused) && <span className="adr-err" role="alert">{(f.choice && refused) || noneRefused || problem!.message}</span>}
      </span>
      <span className="rp-src">
        <span className={`rp-chip${changed ? " is-changed" : source === "this repo" ? " is-own" : ""}`}>{changed ? "changed" : source}</span>
        {set && source === "this repo" && f.key !== "default_chain" && (
          <button type="button" className="rp-reset" aria-label={`Reset ${f.label}`} onClick={() => void send(null)}>Reset</button>
        )}
      </span>
    </div>
  );
}
