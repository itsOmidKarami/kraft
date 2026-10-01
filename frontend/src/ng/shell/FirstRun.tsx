import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { Link } from "react-router-dom";
import * as api from "../../api";
import type { Policy, RepoProbe, TemplateSummary } from "../../types/settings";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import "./first-run.css";

/** The gap between the four probe rows appearing, so a person can read what Kraft found. */
export const PROBE_STEP_MS = 450;

const STEPS = ["Connect a repo", "Chain and policy", "First work item"] as const;
const ADD_REPO_COMMAND = "kraft admin init";

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

function probeRows(p: RepoProbe): [string, string][] {
  return [
    [".gitmodules", p.submodules.length ? `${p.submodules.length} submodule${p.submodules.length === 1 ? "" : "s"}` : "none"],
    [".beads/", p.has_beads ? "found" : "not found"],
    ["Test command", p.test_command ?? "not detected"],
    ["Forge remote", p.forge ? `${p.forge}${p.project ? ` · ${p.project}` : ""}` : "none"],
  ];
}

/** Design 08 / Decisions §10: what the board shows before the first repo is connected. */
export function FirstRun() {
  const [address, setAddress] = useState<string | null>(null);
  useEffect(() => {
    api.getHealth().then((h) => h.bind && h.port != null && setAddress(`${h.bind}:${h.port}`)).catch(() => {});
  }, []);

  const [step, setStep] = useState(1);
  const [reached, setReached] = useState(1);
  const go = (n: number) => { setStep(n); setReached((r) => Math.max(r, n)); };

  const [path, setPath] = useState("");
  const [probing, setProbing] = useState(false);
  const [probe, setProbe] = useState<RepoProbe | null>(null);
  const [shown, setShown] = useState(0);
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!probe || shown >= 4) return;
    const t = setTimeout(() => setShown((s) => s + 1), PROBE_STEP_MS);
    return () => clearTimeout(t);
  }, [probe, shown]);

  const doProbe = async () => {
    if (!path.trim() || probing) return;
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
    if (!probe || adding) return;
    setAdding(true);
    setError(null);
    try {
      await api.addRepo({
        path: probe.path,
        name: probe.name,
        default_chain_template: "default",
        test_command: probe.test_command,
        test_scopes: probe.test_scopes,
        forge: probe.forge,
        project: probe.project,
      });
      setAdded(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAdding(false);
    }
  };

  const chains = useLoad(api.getTemplates);
  const policy = useLoad(api.getPolicy);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(ADD_REPO_COMMAND);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const rows = probe ? probeRows(probe) : [];
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
                <input value={path} spellCheck={false} placeholder="/Users/you/code/project" disabled={added}
                  onChange={(e) => { setPath(e.target.value); setProbe(null); setShown(0); setError(null); }}
                  onKeyDown={(e) => e.key === "Enter" && (probe ? shown >= 4 && !added && doAdd() : doProbe())} />
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
                  <Button variant="primary" disabled={shown < 4 || adding} onClick={doAdd}>{adding ? "Adding…" : "Add repo"}</Button>
                ) : (
                  <Button variant="primary" disabled={!path.trim() || probing} onClick={doProbe}>{probing ? "Probing…" : "+ Add repo"}</Button>
                )}
                {added && probe && <span className="fr-ok">Added {probe.name}</span>}
              </div>
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
              <div className="fr-actions">
                <Link className="btn btn-primary" to="/?new=1">+ New work item</Link>
              </div>
              <h3>Or drive it from an agent session</h3>
              <p><code>{ADD_REPO_COMMAND}</code> registers the MCP server and the /kraft:* skills.</p>
              <Button onClick={copy}><Copy size={14} aria-hidden />{copied ? "Copied" : "Copy command"}</Button>
            </>
          )}
        </div>
        <aside className="fr-aside">
          {step === 1 && <><h3>Kraft probes</h3><p>a local git checkout · .gitmodules, .beads/, the test command and the forge remote</p></>}
          {step === 2 && <><h3>Defaults</h3><p>Every item uses the default chain and the policy below unless it names another. Everything Settings writes is YAML in ~/.kraft/templates.</p></>}
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
