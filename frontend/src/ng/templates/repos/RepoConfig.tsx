import { useState } from "react";
import { Segmented } from "../../ui/Segmented";
import { detailOf } from "../../http";
import type { ConfigDraft } from "../draft/useConfigDraft";
import type { Problem, Result } from "../draft/types";
import { ValueCell } from "../draft/ValueCell";
import { FIELDS, FORGES, patchFor, sourceOf, valueOf, type RepoField } from "./fields";
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
 *  Reset on a value this repo sets. */
export function RepoConfig({ draft, repo, chains }: { draft: ConfigDraft; repo: RepoView; chains: string[] }) {
  const r = draft.view!.result;
  const own = problemsOf(r, repo.path);
  const diff = r.changes.find((c) => c.path === repo.path)?.fields ?? [];
  return (
    <div className="rp-cfg">
      {FIELDS.map((f) => (
        <Row key={f.key} draft={draft} repo={repo} f={f} chains={chains} problem={own.find((p) => p.field === f.key || (f.key.startsWith("policy.") && p.field === f.key.slice(7)))} changed={diff.includes(f.key.split(".")[0])} />
      ))}
    </div>
  );
}

function Row({ draft, repo, f, chains, problem, changed }: { draft: ConfigDraft; repo: RepoView; f: RepoField; chains: string[]; problem?: Problem; changed: boolean }) {
  const v = valueOf(repo, f);
  const shown = f.show(f.key === "steering" ? repo.resolved.steering : v);
  const set = v != null && !(Array.isArray(v) && v.length === 0);
  const source = sourceOf(repo, f);
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
          <Segmented label="forge" value={(typeof v === "string" ? v : "none")} options={FORGES.map((x) => ({ value: x, label: x }))} onChange={(x) => void send(x === "none" ? null : x)} />
        ) : f.choice === "chain" ? (
          <select className="rp-select" aria-label={f.label} value={String(v ?? f.fallback ?? "default")} onChange={(e) => void send(e.target.value)}>
            {[...new Set([String(v ?? "default"), ...chains])].map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        ) : (
          <ValueCell label={f.label} value={f.show(v)} display={shown || f.fallback || "not set"} muted={!shown} bad={!!problem} changed={changed} placeholder={f.placeholder} onCommit={commit} />
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
        {set && source === "this repo" && f.key !== "default_chain_template" && (
          <button type="button" className="rp-reset" aria-label={`Reset ${f.label}`} onClick={() => void send(null)}>Reset</button>
        )}
      </span>
    </div>
  );
}
