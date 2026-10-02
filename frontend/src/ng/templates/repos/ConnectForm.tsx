import { useState } from "react";
import { detailOf, jsonBody, request } from "../../http";
import { Button } from "../../ui/Button";
import type { ConfigDraft } from "../draft/useConfigDraft";
import { Kv } from "../panes/controls";
import type { ProbeCandidate, ProbeStop } from "../../../types/settings";
import { chosenSource, others, readFrom, withSource } from "./evidence";

/** `POST /repos/probe`'s answer, the fields Connect reads. */
export interface Probe {
  path: string;
  name: string;
  branch: string | null;
  test_command: string | null;
  setup_command?: string | null;
  test_scopes: { paths: string[]; command: string }[] | null;
  forge: string | null;
  project: string | null;
  candidates?: ProbeCandidate[];
  read_from?: string | null;
  missing_setup?: string[];
  stopped?: ProbeStop[];
}

/** The `fields` of `add_repo` from a probe: what `POST /repos` writes (Decided 9). A
 *  probe's lone root `["**"]` scope only repeats `test_command`, and would shadow its
 *  later edits, so only a scope with a nested path is kept. */
export function fieldsFrom(p: Probe): Record<string, unknown> {
  const nested = (p.test_scopes ?? []).some((s) => s.paths.join() !== "**");
  const scopes = nested ? p.test_scopes : null;
  return {
    name: p.name,
    default_chain_template: "default",
    test_command: p.test_command,
    ...(scopes ? { test_scopes: scopes } : {}),
    setup_command: p.setup_command ?? null,
    forge: p.forge,
    project: p.project,
    enabled: !!(p.test_command || scopes),
  };
}

/** Connect repo's popover: a path, Check (read-only probe), Connect (`add_repo`). */
export function ConnectForm({ draft, known, onDone }: { draft: ConfigDraft; known: Set<string>; chains: string[]; onDone: (path: string) => void }) {
  const [path, setPath] = useState("");
  const [probe, setProbe] = useState<Probe | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const check = async () => {
    if (!path.trim() || busy) return;
    setBusy(true);
    setError(null);
    setProbe(null);
    const a = await request<Probe>("/repos/probe", jsonBody("POST", { path: path.trim() }));
    setBusy(false);
    if (a.status === 200) setProbe(a.body);
    else setError(detailOf(a.body));
  };
  const connect = async () => {
    if (!probe) return;
    if (known.has(probe.path)) return setError(`${probe.path} is already in the list.`);
    setBusy(true);
    const a = await draft.ops([{ op: "add_repo", path: probe.path, fields: fieldsFrom(probe) }], { quiet: true });
    setBusy(false);
    if (a.status === 200) onDone(probe.path);
    else setError(detailOf(a.body));
  };

  return (
    <form className="rp-connect" onSubmit={(e) => { e.preventDefault(); void (probe ? connect() : check()); }}>
      <label className="rp-connect-label" htmlFor="rp-connect-path">Path to a git repository</label>
      <div className="rp-connect-line">
        <input id="rp-connect-path" className="rp-search" autoFocus spellCheck={false} placeholder="~/src/product" value={path} onChange={(e) => { setPath(e.target.value); setProbe(null); setError(null); }} />
        <Button type="submit" disabled={busy || !path.trim()}>{probe ? "Connect" : "Check"}</Button>
      </div>
      {error && <p className="rp-err" role="alert">{error}</p>}
      {probe && (
        <div className="rp-probe" aria-label="What was found">
          <Kv k="name" v={probe.name} />
          <Kv k="branch" v={probe.branch ?? "—"} mono />
          <Kv k="forge" v={probe.forge ? `${probe.forge}${probe.project ? ` · ${probe.project}` : ""}` : "no forge remote"} muted={!probe.forge} />
          <Kv k="tests" v={probe.test_command ? withSource(probe.test_command, chosenSource(probe.candidates, "test")) : "none found"} mono muted={!probe.test_command} />
          <Kv k="test scopes" v={probe.test_scopes ? `${probe.test_scopes.length} found` : "—"} muted={!probe.test_scopes} />
          <Kv k="setup" v={probe.setup_command ? withSource(probe.setup_command, chosenSource(probe.candidates, "setup")) : "none found"} mono muted={!probe.setup_command} />
          {others(probe.candidates) && <Kv k="also found" v={others(probe.candidates)!} mono muted />}
          {(probe.stopped ?? []).map((s) => <Kv key={s.dir} k="no tests" v={`${s.dir} is ${s.reason}`} muted />)}
          {probe.missing_setup?.length ? <Kv k="no setup" v={`${probe.missing_setup.join(", ")}: tests and nothing to prepare them; the first work item stops until a setup command is set, or No setup needed is ticked, in Templates › Repos`} muted /> : null}
          {readFrom(probe.read_from) && <Kv k="read from" v={readFrom(probe.read_from)!} muted />}
          <p className="rp-connect-note">{fieldsFrom(probe).enabled ? "Connected enabled." : "No tests found: connected disabled until you set a test command."}</p>
        </div>
      )}
    </form>
  );
}
