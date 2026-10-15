import { useEffect, useMemo, useState } from "react";
import { Plus } from "lucide-react";
import { Inspector } from "../graph/Inspector";
import { detailOf, request } from "../http";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Segmented } from "../ui/Segmented";
import { Switch } from "../ui/Switch";
import { AreaFrame } from "../templates/draft/AreaFrame";
import { useConfigDraft, type ConfigDraft } from "../templates/draft/useConfigDraft";
import type { Change, Op, Problem } from "../templates/draft/types";
import { ValueCell } from "../templates/draft/ValueCell";
import { Kv } from "../templates/panes/controls";
import { subscribeLive } from "../live";
import { checkText, hhmm, nextCheck } from "./intake/checks";
import { CRON_PRESETS, cronProblem, describeCron } from "./intake/cron";
import { type Check, type IntakeResolved, type Schedule, intakeOf, problemsOfSchedule, scheduleProblemText } from "./intake/types";
import { BEADS_DOCS, pickup, REMOVE_SCHEDULE } from "./intake/pickup";
import { repoChoice, repoLabel, type NamedRepo } from "./intake/repos";
import { intervalFromText, PRIORITIES, showMinutes, toMinutes } from "./intake/units";
import "./settings.css";
import "./intake/intake.css";

const CHECKS_EVERY_MS = 30_000;

/** `/settings/auto-intake` (AreaIntake): the pickup rule and the schedules, one draft over `intake.yaml`. How many items run at once is Policy › Housekeeping's `max_concurrent`. */
export function IntakePage() {
  const draft = useConfigDraft("intake", "intake");
  if (draft.status === "notFound" || (draft.status === "error" && !draft.view))
    return <div className="tpl-note" role="alert"><h1>Could not load auto-intake</h1><Button onClick={() => void draft.reload()}>Retry</Button></div>;
  if (!draft.view) return <div className="tpl-note" aria-busy="true">Loading auto-intake…</div>;
  return <Editor draft={draft} />;
}

function Editor({ draft }: { draft: ConfigDraft }) {
  const r = draft.view!.result;
  const data = intakeOf(r);
  const [sel, setSel] = useState<number | null>(null);
  const [paneOpen, setPaneOpen] = useState(false);
  const [removing, setRemoving] = useState<Schedule | null>(null);
  const [checks, setChecks] = useState<Check[] | null>(null);
  // The clock "next check in N min" counts on, moved with each read of the checks.
  const [now, setNow] = useState(() => Date.now());
  const [repos, setRepos] = useState<NamedRepo[]>([]);
  const [chains, setChains] = useState<string[]>([]);

  // A live `intake_checked` frame is the check, prepended; the refetch below is the fallback for a missed frame.
  useEffect(() => subscribeLive("intake_checked", (payload) => {
    const c = payload as Check;
    if (!c || typeof c !== "object" || !("ready" in c)) return;
    setNow(Date.now());
    setChecks((prev) => [c, ...(prev ?? []).filter((x) => x.id !== c.id)].slice(0, 20));
  }), []);
  useEffect(() => {
    let live = true;
    const load = () => void request<Check[]>("/intake/checks?limit=20").then((a) => {
      if (!live) return;
      setNow(Date.now());
      if (a.status === 200 && Array.isArray(a.body)) setChecks(a.body);
    });
    load();
    const t = setInterval(load, CHECKS_EVERY_MS);
    window.addEventListener("focus", load);
    return () => { live = false; clearInterval(t); window.removeEventListener("focus", load); };
  }, []);
  useEffect(() => {
    void request<{ repos: NamedRepo[] }>("/repos").then((a) => { if (a.status === 200) setRepos(a.body.repos ?? []); });
    void request<{ id: string }[]>("/templates/chains").then((a) => { if (a.status === 200 && Array.isArray(a.body)) setChains(a.body.map((c) => c.id)); });
  }, []);

  const changes = useMemo(() => new Map<string, Change>(r.changes.map((c) => [`${c.file ?? ""}|${c.path}`, c])), [r.changes]);
  const area = useMemo(() => ({
    crumb: "Auto-intake",
    files: ["intake.yaml"],
    toast: "Published auto-intake · applies now",
    affects: () => <Kv k="applies" v="without a restart: the poller is replaced and the new interval and schedules are live" />,
  }), []);

  const schedules = data?.schedules ?? [];
  // The live poller runs the published interval: with that or on/off changed in the draft, its next check isn't the draft's to say.
  const unpublished = changes.has("intake.yaml|interval_s") || changes.has("intake.yaml|enabled");
  const next = data?.enabled && !unpublished ? nextCheck(checks?.[0]?.at, data.interval_s, now) : null;
  const chosen = sel === null ? undefined : schedules.find((s) => s.index === sel);
  const onFix = (p: Problem) => {
    setSel(p.schedule ?? null);
    if (p.schedule !== undefined) setPaneOpen(true);
  };

  const addSchedule = async () => {
    const repo = repos[0]?.path ?? "";
    const a = await draft.ops([{ op: "add_schedule", cron: "0 9 * * 1", repo, chain: chains.includes("default") ? "default" : chains[0] ?? "default", title: "Scheduled item" }]);
    if (a.status === 200) {
      const now = intakeOf((a.body as unknown as { result: typeof r }).result);
      setSel((now?.schedules.length ?? 1) - 1);
      setPaneOpen(true);
    }
  };
  const removeSchedule = async (index: number) => {
    const a = await draft.ops([{ op: "remove_schedule", index }]);
    if (a.status === 200) setSel(null);
  };

  return (
    <AreaFrame draft={draft} area={area} pageKey="intake" title="Auto-intake" onFix={onFix}>
      {({ size, review, reserve }) => (
        <>
          <div className="ink-body" style={{ right: chosen || review ? reserve(paneOpen) : 0 }}>
            <div className="ink-page">
              {!data ? (
                <div className="tpl-note" role="alert">
                  <h1>intake.yaml does not load</h1>
                  {r.problems[0] && <p>{r.problems[0].message}</p>}
                  <p>Open YAML in the header to repair it.</p>
                </div>
              ) : (
                <>
                  <section className={`ink-card${data.enabled ? " is-on" : ""}`} aria-label="Pickup">
                    <div className="ink-status">
                      <span className="ink-dot" aria-hidden />
                      <span>{data.enabled ? `On${next ? ` · ${next}` : ""}` : "Off"}</span>
                      {changes.has("intake.yaml|enabled") && <span className="ink-chip is-changed">changed</span>}
                      <span className="ink-gap" />
                      <Switch label="enabled" checked={data.enabled} onChange={(on) => void send(draft, { op: "set_intake", patch: { enabled: on } })} />
                    </div>
                    <p className="ink-sentence">{pickup(data)}</p>
                    <p className="ink-note">Issues come from <a className="ink-link" href={BEADS_DOCS} target="_blank" rel="noopener noreferrer">beads</a>, the optional bd tracker; a repo without one has nothing to pick up.</p>
                    <PickupRows draft={draft} i={data} repos={repos} changes={changes} />
                  </section>

                  <section aria-label="Recent checks">
                    <h2 className="ink-h2">Recent checks</h2>
                    <ul className="ink-checks">
                      {!data.enabled ? <li className="ink-muted">Auto-intake is off.</li>
                        : checks === null ? <li className="ink-muted">Loading…</li>
                        : checks.length === 0 ? <li className="ink-muted">No checks yet.</li>
                        : checks.map((c) => <li key={c.id}><span className="ink-time">{hhmm(c.at)}</span><span>{checkText(c, data.priority_ceiling)}</span></li>)}
                    </ul>
                  </section>

                  <section aria-label="Schedules">
                    <div className="ink-head">
                      <h2 className="ink-h2">Schedules</h2>
                      <Button onClick={() => void addSchedule()}><Plus size={13} aria-hidden /> Add schedule</Button>
                    </div>
                    <ul className="ink-sched">
                      {schedules.map((s) => {
                        const bad = problemsOfSchedule(r, s.index);
                        return (
                          <li key={s.index}>
                            <button type="button" className={`ink-sch${sel === s.index ? " is-sel" : ""}`} aria-pressed={sel === s.index} onClick={() => { setSel(s.index); setPaneOpen(true); }}>
                              <span className="ink-sch-name">{s.title || "untitled"}{bad.length > 0 && <span className="ink-bad" role="img" aria-label="has a problem" />}</span>
                              <span className="ink-sch-line">{describeCron(s.cron)} · {repoLabel(s.repo, repos)} · {s.chain}</span>
                              <code className="ink-sch-cron">{s.cron}</code>
                            </button>
                            {bad.map((p, i) => <p key={i} className="ink-err" role="alert">{scheduleProblemText(p, s)}</p>)}
                          </li>
                        );
                      })}
                    </ul>
                    {schedules.length === 0 && <p className="ink-muted">No schedules yet.</p>}
                  </section>
                  <p className="ink-note">Auto-intake removes the typing, not the judgement: a started item passes no gate by itself. Applies without a restart once published.</p>
                </>
              )}
            </div>
          </div>
          {!review && data && chosen && (
            <Inspector
              id="intake-pane"
              open={paneOpen}
              size={size}
              crumbs={[{ label: "Auto-intake", onClick: () => setSel(null) }]}
              icon="clock"
              title={chosen.title || "untitled"}
              sub="schedule"
              onCollapse={() => setPaneOpen(false)}
              onExpand={() => setPaneOpen(true)}
              footer={<><span className="bp-gap" /><Button variant="danger" onClick={() => setRemoving(chosen)}>Remove schedule</Button></>}
            >
              <ScheduleRows key={chosen.index} draft={draft} s={chosen} repos={repos} chains={chains} changes={changes} problems={problemsOfSchedule(r, chosen.index)} />
            </Inspector>
          )}
          {removing && (
            <Dialog
              title={`Remove ${removing.title || "this schedule"}?`}
              onClose={() => setRemoving(null)}
              footer={<><span className="bp-gap" /><Button onClick={() => setRemoving(null)}>Cancel</Button><Button variant="danger" onClick={() => { void removeSchedule(removing.index); setRemoving(null); }}>Remove schedule</Button></>}
            >
              <p>{REMOVE_SCHEDULE}</p>
            </Dialog>
          )}
        </>
      )}
    </AreaFrame>
  );
}

function Row({ k, children, note, changed }: { k: string; children: React.ReactNode; note?: string; changed?: boolean }) {
  return (
    <div className="ink-row">
      <span className="ink-k">{k}</span>
      <span className="ink-v">{children}</span>
      {changed && <span className="ink-chip is-changed">changed</span>}
      {note && <span className="ink-rownote">{note}</span>}
    </div>
  );
}

/** One `set_intake` or `set_schedule` write; the refusal's message or null. */
async function send(draft: ConfigDraft, op: Op): Promise<string | null> {
  const a = await draft.ops([op], { quiet: true });
  return a.status === 200 ? null : detailOf(a.body);
}

function PickupRows({ draft, i, repos, changes }: { draft: ConfigDraft; i: IntakeResolved; repos: NamedRepo[]; changes: Map<string, Change> }) {
  const set = (patch: Record<string, unknown>) => send(draft, { op: "set_intake", patch });
  const changed = (file: string, key: string) => changes.has(`${file}|${key}`);
  return (
    <div>
      <Row k="check every" changed={changed("intake.yaml", "interval_s")}>
        <ValueCell label="check every, minutes" value={String(toMinutes(i.interval_s))} display={showMinutes(i.interval_s)} onCommit={(t) => { const v = intervalFromText(t); return "error" in v ? v.error : set({ interval_s: v.seconds }); }} />
      </Row>
      <Row k="priority at or below" changed={changed("intake.yaml", "priority_ceiling")}>
        <Segmented label="priority at or below" value={String(i.priority_ceiling)} options={PRIORITIES} onChange={(v) => void set({ priority_ceiling: Number(v) })} />
      </Row>
      <Row k="repos" changed={changed("intake.yaml", "repos")}>
        <ValueCell label="repos" noun="repo" value={i.repos.join(", ")} display={i.repos.length ? i.repos.join(", ") : "every enabled repo"} muted={!i.repos.length} placeholder={repos.map((r) => r.path).join(", ") || "every enabled repo"} choices={repos.map((r) => ({ value: r.path, summary: r.name ?? undefined }))} closed multiple onCommit={(t) => set({ repos: t.split(",").map((x) => x.trim()).filter(Boolean) })} />
      </Row>
    </div>
  );
}

function ScheduleRows({ draft, s, repos, chains, changes, problems }: { draft: ConfigDraft; s: Schedule; repos: NamedRepo[]; chains: string[]; changes: Map<string, Change>; problems: Problem[] }) {
  const patch = (p: Record<string, unknown>) => send(draft, { op: "set_schedule", index: s.index, patch: p });
  const changed = (field: string) => changes.has(`intake.yaml|schedules.${s.index}.${field}`);
  const text = (field: keyof Schedule, label: string, k = label) => (
    <Row k={k} changed={changed(field)}>
      <ValueCell label={label} value={String(s[field] ?? "")} display={String(s[field] ?? "") || "not set"} muted={!s[field]} onCommit={(t) => (t ? patch({ [field]: t }) : field === "description" ? patch({ description: "" }) : "This cannot be empty.")} />
      {problems.filter((p) => p.field === field).map((p, n) => <span key={n} className="ink-err" role="alert">{scheduleProblemText(p, s)}</span>)}
    </Row>
  );
  return (
    <div>
      {text("title", "name")}
      <Row k="runs" changed={changed("cron")}>
        <CronPicker cron={s.cron} onPick={(cron) => patch({ cron })} />
        {problems.filter((p) => p.field === "cron").map((p, n) => <span key={n} className="ink-err" role="alert">{scheduleProblemText(p, s)}</span>)}
      </Row>
      <Row k="repo" changed={changed("repo")}>
        <select className="ink-select" aria-label="repo" value={s.repo} onChange={(e) => void patch({ repo: e.target.value })}>
          {[...new Set([s.repo, ...repos.map((r) => r.path)])].filter(Boolean).map((p) => <option key={p} value={p}>{repoChoice(p, repos)}</option>)}
        </select>
        {problems.filter((p) => p.field === "repo").map((p, n) => <span key={n} className="ink-err" role="alert">{p.message}</span>)}
      </Row>
      <Row k="chain" changed={changed("chain")}>
        <select className="ink-select" aria-label="chain" value={s.chain} onChange={(e) => void patch({ chain: e.target.value })}>
          {[...new Set([s.chain, ...chains])].map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {problems.filter((p) => p.field === "chain").map((p, n) => <span key={n} className="ink-err" role="alert">{p.message}</span>)}
      </Row>
      {text("description", "description")}
      <p className="ink-note">Auto-intake removes the typing, not the judgement: a started item passes no gate by itself.</p>
    </div>
  );
}

const CUSTOM = "custom";

/** A schedule's timing: a preset, or a cron of its own, with the sentence it reads as. A cron the scheduler would refuse is explained here and not saved. */
function CronPicker({ cron, onPick }: { cron: string; onPick: (cron: string) => Promise<string | null> }) {
  const preset = CRON_PRESETS.some((p) => p.cron === cron);
  const [custom, setCustom] = useState(!preset);
  const [error, setError] = useState<string | null>(null);
  const pick = async (v: string) => {
    if (v === CUSTOM) return setCustom(true);
    setCustom(false);
    setError(await onPick(v));
  };
  return (
    <>
      <select className="ink-select" aria-label="runs" value={custom ? CUSTOM : cron} onChange={(e) => void pick(e.target.value)}>
        {CRON_PRESETS.map((p) => <option key={p.cron} value={p.cron}>{p.label}</option>)}
        <option value={CUSTOM}>Custom cron…</option>
      </select>
      {custom && <ValueCell label="cron" value={cron} display={cron || "not set"} muted={!cron} onCommit={(t) => cronProblem(t) ?? onPick(t.trim().split(/\s+/).join(" "))} />}
      {error && <span className="ink-err" role="alert">{error}</span>}
      <span className="ink-says">{describeCron(cron)}</span>
    </>
  );
}
