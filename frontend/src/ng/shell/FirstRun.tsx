import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { Link } from "react-router-dom";
import * as api from "../../api";
import type { Policy, RepoProbe, TemplateSummary } from "../../types/settings";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { onMac } from "../keys";
import { missingLine, NO_COMMIT_WHY, others, readFrom, setupLine, stopLine, testsLine } from "../templates/repos/evidence";
import "./first-run.css";

/** The gap between the probe rows appearing, so a person can read what Kraft found. */
export const PROBE_STEP_MS = 450;

const STEPS = ["Connect a repo", "Chain and policy", "First work item"] as const;
/** What `docsite/content/1.get-started/1.install.md` gives for Claude Code: the
 *  plugin, which registers the MCP server every Claude worker needs. */
const PLUGIN_COMMANDS = "claude plugin marketplace add itsOmidKarami/kraft\nclaude plugin install kraft@kraft";

/** Where the wizard got to once a repo is added, kept until its last step is
 *  done or skipped: with a repo connected the board no longer shows it on its
 *  own, and step 3 is the one that says Claude workers need Kraft registered.
 *  So a reload, or a visit to Templates › Repos from step 1, comes back to it. */
const SAVED = "kraft.firstRun";
type Saved = { step: number; reached: number; path: string; name: string; disabled: boolean; noSetup?: boolean; repoPath?: string; missing?: string[] };

export function savedFirstRun(): Saved | null {
  try {
    const v = JSON.parse(localStorage.getItem(SAVED) ?? "null");
    return v && typeof v.step === "number" ? v : null;
  } catch {
    return null;
  }
}
export function clearFirstRun() {
  try {
    localStorage.removeItem(SAVED);
  } catch {
    /* storage blocked: nothing was kept */
  }
}
function keep(s: Saved) {
  try {
    localStorage.setItem(SAVED, JSON.stringify(s));
  } catch {
    /* storage blocked: the wizard lasts until the next reload */
  }
}

type Load<T> = { state: "loading" } | { state: "error" } | { state: "ready"; value: T };

function useLoad<T>(fetcher: () => Promise<T>): Load<T> {
  const [v, setV] = useState<Load<T>>({ state: "loading" });
  useEffect(() => {
    let live = true;
    fetcher().then((value) => live && setV({ state: "ready", value }), () => live && setV({ state: "error" }));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return v;
}

function StepCircle({ n, state, onClick }: { n: number; state: "done" | "current" | "todo"; onClick?: () => void }) {
  const inner = state === "done" ? <Check size={12} aria-hidden /> : n;
  const label = `Step ${n}: ${STEPS[n - 1]}`;
  if (!onClick) return <span className={`fr-circle fr-${state}`} aria-label={label} role="img">{inner}</span>;
  return (
    <button type="button" className={`fr-circle fr-${state}`} aria-label={label} aria-current={state === "current" ? "step" : undefined} onClick={onClick}>
      {inner}
    </button>
  );
}

function also(p: RepoProbe, role: "test" | "setup", label: string): [string, string][] {
  const saved = [p.test_command, p.setup_command, ...(p.test_scopes ?? []).map((s) => s.command)];
  const found = others(p.candidates, role, saved);
  return found ? [[label, found]] : [];
}

function probeRows(p: RepoProbe): [string, string][] {
  return [
    [".gitmodules", p.submodules.length ? `${p.submodules.length} submodule${p.submodules.length === 1 ? "" : "s"}` : "none"],
    [".beads/", p.has_beads ? "found" : "not found"],
    ["Test command", testsLine(p)],
    // A monorepo's other scopes: without them the one command above reads as the repo's whole suite.
    ...(p.test_scopes ?? []).filter((s) => s.paths.length === 1 && s.paths[0] !== "**").map((s) => [`Tests in ${s.paths[0]}`, s.command] as [string, string]),
    ["Setup command", setupLine(p)],
    ["Forge remote", p.forge ? `${p.forge}${p.project ? ` · ${p.project}` : ""}` : "none"],
    ...also(p, "test", "Also found for tests"),
    ...also(p, "setup", "Also found for setup"),
    ...(p.stopped ?? []).map((s) => ["No tests", stopLine(s)] as [string, string]),
    ...(p.missing_setup?.length
      ? [["No setup", "the first work item stops until a setup command is set, or No setup needed is ticked, in Templates › Repos"] as [string, string]]
      : []),
    ...(missingLine(p.missing_tools) ? [["Not installed", missingLine(p.missing_tools)!] as [string, string]] : []),
    ...(readFrom(p.read_from) ? [["Read from", readFrom(p.read_from)!] as [string, string]] : []),
  ];
}

/** Design 08 / Decisions §10: what the board shows before the first repo is
 *  connected, and after, until its last step is done or skipped (`onDone`). */
export function FirstRun({ onDone }: { onDone?: () => void }) {
  const [address, setAddress] = useState<string | null>(null);
  useEffect(() => {
    api.getHealth().then((h) => h.bind && h.port != null && setAddress(`${h.bind}:${h.port}`)).catch(() => {});
  }, []);

  const [saved] = useState(savedFirstRun);
  const [step, setStep] = useState(saved?.step ?? 1);
  const [reached, setReached] = useState(saved?.reached ?? 1);
  const go = (n: number) => { setStep(n); setReached((r) => Math.max(r, n)); };

  const [path, setPath] = useState(saved?.path ?? "");
  const [probing, setProbing] = useState(false);
  const [probe, setProbe] = useState<RepoProbe | null>(null);
  const [shown, setShown] = useState(0);
  const [adding, setAdding] = useState(false);
  /** The added repo's name, once it is added. */
  const [added, setAdded] = useState(saved?.name ?? "");
  /** The server saved the repo disabled: no test command, so the composer will not list it. */
  const [disabled, setDisabled] = useState(saved?.disabled ?? false);
  /** The server saved no setup command: the repo's first item stops before it starts until one is set. */
  const [noSetup, setNoSetup] = useState(saved?.noSetup ?? false);
  /** The resolved path of the added repo, and its directories with tests and no setup. */
  const [repoPath, setRepoPath] = useState(saved?.repoPath ?? "");
  const [missing, setMissing] = useState<string[]>(saved?.missing ?? []);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (added) keep({ step, reached, path, name: added, disabled, noSetup, repoPath, missing });
  }, [added, step, reached, path, disabled, noSetup, repoPath, missing]);
  // Fixed in Templates › Repos and back here: what it said is read again, not kept stale.
  const reread0 = useRef(false);
  useEffect(() => {
    if (!repoPath || (!disabled && !noSetup)) return;
    const reread = () => {
      if (document.visibilityState === "hidden") return;
      api.getRepos().then(({ repos }) => {
        const entry = repos.find((r) => r.path === repoPath);
        if (!entry) return;
        setDisabled(entry.enabled === false);
        setNoSetup(entry.setup_command == null);
      }, () => {});
    };
    // And once on arriving: the wizard's own Templates › Repos link and the
    // sidebar are in-app routes, so fixing it there and coming back fires
    // neither event (R9a-06).
    if (!reread0.current) {
      reread0.current = true;
      reread();
    }
    window.addEventListener("focus", reread);
    document.addEventListener("visibilitychange", reread);
    return () => {
      window.removeEventListener("focus", reread);
      document.removeEventListener("visibilitychange", reread);
    };
  }, [repoPath, disabled, noSetup]);
  const finish = () => {
    clearFirstRun();
    onDone?.();
  };

  useEffect(() => {
    if (!probe || shown >= probeRows(probe).length) return;
    const t = setTimeout(() => setShown((s) => s + 1), PROBE_STEP_MS);
    return () => clearTimeout(t);
  }, [probe, shown]);

  // A probe swaps the button that started it (+ Add repo, Check again) for another, so
  // focus returns to the path field, where Enter adds the repo once its rows are read,
  // not to the page (review L1, R11a-08).
  const pathField = useRef<HTMLInputElement>(null);
  const refocus = useRef(false);
  useEffect(() => {
    if (!refocus.current || probing) return;
    refocus.current = false;
    pathField.current?.focus();
  }, [probing]);
  const doProbe = async () => {
    if (!path.trim() || probing) return;
    refocus.current = true;
    setProbing(true);
    setError(null);
    setProbe(null);
    setShown(0);
    try {
      setProbe(await api.probeRepo(path.trim()));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setProbing(false);
    }
  };
  const doAdd = async () => {
    if (!probe || adding || probe.read_from === null) return;
    setAdding(true);
    setError(null);
    // As Templates › Repos' Connect does: a probe's lone root `["**"]` scope only repeats
    // `test_command` and would shadow its later edits, and an empty list is not a valid
    // `test_scopes`, so only scopes with a nested path are sent.
    const nested = (probe.test_scopes ?? []).some((s) => s.paths.join() !== "**");
    try {
      const repo = await api.addRepo({
        path: probe.path,
        name: probe.name,
        default_chain_template: "default",
        test_command: probe.test_command,
        ...(nested ? { test_scopes: probe.test_scopes } : {}),
        forge: probe.forge,
        project: probe.project,
      });
      setDisabled(repo.enabled === false);
      setNoSetup(repo.setup_command === null);
      setRepoPath(probe.path);
      setMissing(probe.missing_setup ?? []);
      setAdded(probe.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAdding(false);
    }
  };

  /** Directories below the root with tests and no setup: "No setup needed" would leave them unprepared. */
  const nested = missing.filter((d) => d !== ".").map((d) => `${d}/`);
  const chains = useLoad(api.getTemplates);
  const policy = useLoad(api.getPolicy);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(PLUGIN_COMMANDS);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const rows = probe ? probeRows(probe) : [];
  const probed = probe != null && shown >= rows.length;
  // A repo with no commit cannot be added yet: the reason sits by the button it turns off,
  // and Check again (or Enter) reads it again without editing the path (R10a-01).
  const noCommit = probe?.read_from === null;
  const circle = (n: number) => (
    <StepCircle key={n} n={n} state={n === step ? "current" : n < step || (n === 1 && added) ? "done" : "todo"} onClick={n <= reached ? () => setStep(n) : undefined} />
  );

  return (
    <div className="ng-firstrun">
      <h1>Nothing on the board yet</h1>
      <p className="fr-lead">
        Kraft is running{address && <> at <code>{address}</code></>} with the default chain and policy. Connect a repo and file the first work item; it is created paused, so nothing runs until you start it.
      </p>
      <ol className="fr-steps" aria-label="Setup steps">
        {STEPS.map((label, i) => (
          <li key={label} className={i + 1 === step ? "fr-step-on" : undefined}>
            {circle(i + 1)}
            <span>{label}</span>
          </li>
        ))}
      </ol>

      <section className="fr-card" aria-label={STEPS[step - 1]}>
        <div className="fr-main">
          {step === 1 && (
            <>
              <h2>Connect a repo</h2>
              <Field label="Path to a local git checkout" error={error}>
                <input ref={pathField} value={path} spellCheck={false} placeholder={onMac() ? "/Users/you/code/project" : "/home/you/code/project"} disabled={!!added}
                  onChange={(e) => { setPath(e.target.value); setProbe(null); setShown(0); setError(null); }}
                  onKeyDown={(e) => e.key === "Enter" && (probe && !noCommit ? probed && !added && doAdd() : doProbe())} />
              </Field>
              {probe && (
                <ul className="fr-probes" aria-label="Probe results">
                  {rows.map(([k, v], i) => (
                    <li key={k}><span>{k}</span><span className={i < shown ? undefined : "fr-pending"}>{i < shown ? v : "checking…"}</span></li>
                  ))}
                </ul>
              )}
              <div className="fr-actions">
                {added ? (
                  <Button variant="primary" onClick={() => go(2)}>Continue</Button>
                ) : probe ? (
                  <>
                    <Button variant="primary" disabled={!probed || adding || noCommit} aria-describedby={noCommit && probed ? "fr-no-commit" : undefined} onClick={doAdd}>{adding ? "Adding…" : "Add repo"}</Button>
                    {noCommit && probed && <Button disabled={probing} onClick={doProbe}>Check again</Button>}
                    {noCommit && probed && <span id="fr-no-commit" className="fr-why" role="status">{NO_COMMIT_WHY}</span>}
                  </>
                ) : (
                  <Button variant="primary" disabled={!path.trim() || probing} onClick={doProbe}>{probing ? "Probing…" : "+ Add repo"}</Button>
                )}
                {added && <span className="fr-ok">Added {added}</span>}
              </div>
              {added && disabled && <p>No test command: connected disabled. In <Link to="/templates/repos" className="fr-link">Templates › Repos</Link>, set its test command and Enable it, then publish.</p>}
              {added && noSetup && (nested.length
                ? <p>No setup command: {nested.join(", ")} {nested.length > 1 ? "have" : "has"} tests and nothing found prepares {nested.length > 1 ? "them" : "it"}. Its first work item stops before it starts until you set a setup command that does, in <Link to="/templates/repos" className="fr-link">Templates › Repos</Link>.</p>
                : <p>No setup command found: its first work item stops before it starts until you set one, or tick No setup needed, in <Link to="/templates/repos" className="fr-link">Templates › Repos</Link>.</p>)}
            </>
          )}
          {step === 2 && (
            <>
              <h2>Chain and policy</h2>
              <dl className="fr-facts">
                <dt>Chain</dt>
                <dd>{chains.state === "loading" ? "reading…" : chains.state === "error" ? "Could not read the chains." : chainLine(chains.value)}</dd>
                <dt>Policy</dt>
                <dd>{policy.state === "loading" ? "reading…" : policy.state === "error" ? "Could not read the policy." : policyLine(policy.value, chains)}</dd>
              </dl>
              <div className="fr-actions">
                <Button variant="primary" onClick={() => go(3)}>Continue</Button>
                <Link to="/templates/chains" className="fr-link">Open Chains</Link>
              </div>
            </>
          )}
          {step === 3 && (
            <>
              <h2>First work item</h2>
              <p>It is created paused, so nothing runs until you start it.</p>
              {disabled && added && <p>{added} is disabled, so New work item cannot file to it yet. Set its test command and Enable it in <Link to="/templates/repos" className="fr-link">Templates › Repos</Link> first.</p>}
              <div className="fr-actions">
                <Link className="btn btn-primary" to="/?new=1" onClick={finish}>+ New work item</Link>
                <Button onClick={finish}>Go to the board</Button>
              </div>
              <h3>Before you start it: register Kraft with Claude Code</h3>
              <p>Claude workers need Kraft's MCP server, or Kraft refuses to launch them. Install the Kraft plugin, which also adds the /kraft:* skills:</p>
              <pre className="fr-cmd">{PLUGIN_COMMANDS}</pre>
              <Button onClick={copy}><Copy size={14} aria-hidden />{copied ? "Copied" : "Copy commands"}</Button>
              <p>Then open a Claude Code session in your repo and run <code>/kraft:onboard</code>. The repo is connected already: it checks the setup and test commands against the repo's own docs and CI, offers to rehearse them with <code>kraft repo connect --verify</code>, and confirms <code>kraft admin doctor</code> passes.</p>
              <p>Or, without the plugin, run <code>kraft admin init</code>. Not both.</p>
            </>
          )}
        </div>
        <aside className="fr-aside">
          {step === 1 && <><h3>Kraft probes</h3><p>a local git checkout · .gitmodules, .beads/, the test and setup commands and the forge remote</p></>}
          {step === 2 && <><h3>Defaults</h3><p>Every item uses the default chain and the policy below unless it names another. Everything Settings writes is YAML in $KRAFT_HOME/templates (~/.kraft/templates by default).</p></>}
          {step === 3 && <><h3>On create</h3><p>Filed from a title, or from an existing spec or plan. It waits for you to start it.</p></>}
        </aside>
      </section>
    </div>
  );
}

function chainLine(list: TemplateSummary[]): string {
  const c = list.find((t) => t.id === "default");
  return c ? `default · ${c.nodes.length} nodes, ${c.gates} gates` : "No default chain is installed.";
}
/** The cap of the default chain's first fix loop, else the policy default. */
function policyLine(p: Policy, chains: Load<TemplateSummary[]>): string {
  const key = chains.state === "ready" ? chains.value.find((t) => t.id === "default")?.nodes.find((n) => n.fix_loop)?.fix_loop : undefined;
  const attempts = ((key && p.loops[key]) || p.default).attempts;
  const usd = p.budget?.work_item_usd;
  return `${attempts} fix attempts, ${usd == null ? "no dollar cap per item" : `$${usd} per item`}`;
}
