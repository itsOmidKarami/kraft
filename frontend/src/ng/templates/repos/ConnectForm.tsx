import { useState } from "react";
import { detailOf, jsonBody, request } from "../../http";
import { Button } from "../../ui/Button";
import type { ConfigDraft } from "../draft/useConfigDraft";
import { Kv } from "../panes/controls";
import type { MissingTool, ProbeCandidate, ProbeStop } from "../../../types/settings";
import { chosenSource, missingLine, others, readFrom, setupLine, stopLine, withSource } from "./evidence";

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
  missing_tools?: MissingTool[];
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

/** What checking a connected repo again saves (`set_repo`), as `kraft repo connect`
 *  does: only what its entry leaves undecided, a test command where it has neither one
 *  nor test scopes and a setup command where none is declared. A repo disabled for want
 *  of a test command is enabled once it has one; nothing it already has is changed. */
export function gainsFrom(entry: Record<string, unknown>, p: Probe): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  const scopes = Array.isArray(entry.test_scopes) ? entry.test_scopes : [];
  if (entry.test_command == null && !scopes.length && p.test_command != null) {
    patch.test_command = p.test_command;
    if ((p.test_scopes ?? []).some((s) => s.paths.join() !== "**")) patch.test_scopes = p.test_scopes;
  }
  if (entry.setup_command == null && p.setup_command != null) patch.setup_command = p.setup_command;
  if ("test_command" in patch && entry.enabled === false) patch.enabled = true;
  return patch;
}

const GAINED: Record<string, string> = { test_command: "test command", test_scopes: "test scopes", setup_command: "setup command" };

/** "its test command and setup command, and enables it". */
function gainedLine(patch: Record<string, unknown>): string {
  const named = Object.keys(patch).filter((k) => k in GAINED).map((k) => GAINED[k]);
  return `its ${named.join(" and ")}${patch.enabled ? ", and enables it" : ""}`;
}

/** Connect repo's popover: a path, Check (read-only probe), Connect (`add_repo`), or
 *  Update (`set_repo`) for a repo already connected that leaves something undecided. */
export function ConnectForm({ draft, known, entries = {}, onDone }: { draft: ConfigDraft; known: Set<string>; entries?: Record<string, Record<string, unknown>>; chains: string[]; onDone: (path: string) => void }) {
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
  const entry = probe ? entries[probe.path] : undefined;
  const gains = probe && entry ? gainsFrom(entry, probe) : null;
  const connect = async () => {
    if (!probe || probe.read_from === null) return;
    if (gains && !Object.keys(gains).length) return setError(`${probe.path} is already connected, and nothing it leaves undecided was found.`);
    if (!gains && known.has(probe.path)) return setError(`${probe.path} is already in the list.`);
    setBusy(true);
    const op = gains ? { op: "set_repo", path: probe.path, patch: gains } : { op: "add_repo", path: probe.path, fields: fieldsFrom(probe) };
    const a = await draft.ops([op], { quiet: true });
    setBusy(false);
    if (a.status === 200) onDone(probe.path);
    else setError(detailOf(a.body));
  };

  const stopped = !!probe?.stopped?.length;
  const saved = probe ? [probe.test_command, probe.setup_command, ...(probe.test_scopes ?? []).map((s) => s.command)] : [];
  const alsoTest = probe ? others(probe.candidates, "test", saved) : null;
  const alsoSetup = probe ? others(probe.candidates, "setup", saved) : null;

  return (
    <form className="rp-connect" onSubmit={(e) => { e.preventDefault(); void (probe ? connect() : check()); }}>
      <label className="rp-connect-label" htmlFor="rp-connect-path">Path to a git repository</label>
      <div className="rp-connect-line">
        <input id="rp-connect-path" className="rp-search" autoFocus spellCheck={false} placeholder="~/src/product" value={path} onChange={(e) => { setPath(e.target.value); setProbe(null); setError(null); }} />
        <Button type="submit" disabled={busy || !path.trim() || probe?.read_from === null || (!!gains && !Object.keys(gains).length)}>{gains ? "Update" : probe ? "Connect" : "Check"}</Button>
      </div>
      {error && <p className="rp-err" role="alert">{error}</p>}
      {probe && (
        <div className="rp-probe" aria-label="What was found">
          <Kv k="name" v={probe.name} />
          <Kv k="branch" v={probe.branch ?? "—"} mono />
          <Kv k="forge" v={probe.forge ? `${probe.forge}${probe.project ? ` · ${probe.project}` : ""}` : "no forge remote"} muted={!probe.forge} />
          <Kv k="tests" v={probe.test_command ? withSource(probe.test_command, chosenSource(probe.candidates, "test")) : stopped ? "stopped" : "none found"} mono muted={!probe.test_command} />
          <Kv k="test scopes" v={stopped ? "stopped" : probe.test_scopes ? `${probe.test_scopes.length} found` : "—"} muted={!probe.test_scopes?.length} />
          {/* Every command Connect saves is shown: a nested scope's is not the test command above. */}
          {(probe.test_scopes ?? []).filter((s) => s.paths.length === 1 && s.paths[0] !== "**").map((s) => (
            <Kv key={s.paths[0]} k={s.paths[0]} v={s.command} mono />
          ))}
          <Kv k="setup" v={setupLine(probe)} mono muted={!probe.setup_command} />
          {(probe.stopped ?? []).map((s) => <Kv key={s.dir} k="no tests" v={stopLine(s)} muted />)}
          {probe.missing_setup?.length ? <Kv k="no setup" v="the first work item stops until a setup command is set, or No setup needed is ticked, in Templates › Repos" muted /> : null}
          {missingLine(probe.missing_tools) && <Kv k="not installed" v={missingLine(probe.missing_tools)!} />}
          {readFrom(probe.read_from) && <Kv k="read from" v={readFrom(probe.read_from)!} muted />}
          {alsoTest && <p className="rp-connect-note">Also found for tests: {alsoTest}</p>}
          {alsoSetup && <p className="rp-connect-note">Also found for setup: {alsoSetup}</p>}
          {gains && <p className="rp-connect-note">{Object.keys(gains).length ? `Already connected: Update saves ${gainedLine(gains)}.` : "Already connected, and nothing it leaves undecided was found."}</p>}
          {!gains && <p className="rp-connect-note">{fieldsFrom(probe).enabled ? "Connected enabled." : stopped ? "Tests stopped: connected disabled until you set a test command." : "No tests found: connected disabled until you set a test command."}</p>}
        </div>
      )}
    </form>
  );
}
