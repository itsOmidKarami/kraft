import { useEffect, useMemo, useState } from "react";
import { Plus } from "lucide-react";
import { Inspector } from "../graph/Inspector";
import { detailOf, request } from "../http";
import { Button } from "../ui/Button";
import { Segmented } from "../ui/Segmented";
import { AreaFrame } from "../templates/draft/AreaFrame";
import { useConfigDraft, type ConfigDraft } from "../templates/draft/useConfigDraft";
import type { Change, Op, Problem } from "../templates/draft/types";
import { ValueCell } from "../templates/draft/ValueCell";
import { Kv } from "../templates/panes/controls";
import { checkText, hhmm } from "./intake/checks";
import { describeCron } from "./intake/cron";
import { type Check, type IntakeResolved, type Schedule, intakeOf, problemsOfSchedule } from "./intake/types";
import { intervalFromText, PRIORITIES, showMinutes, toMinutes } from "./intake/units";
import "./intake/intake.css";

const CHECKS_EVERY_MS = 30_000;
type Sel = "pickup" | number;

/** `/settings/auto-intake` (AreaIntake): the pickup rule and the schedules, one draft over `intake.yaml` and `policy.yaml`. */
export function IntakePage() {
  const draft = useConfigDraft("intake", "intake");
  if (draft.status === "notFound" || (draft.status === "error" && !draft.view))
    return <div className="tpl-note" role="alert"><h1>Could not load auto-intake</h1><Button onClick={() => void draft.reload()}>Retry</Button></div>;
  if (!draft.view) return <div className="tpl-note" aria-busy="true">Loading auto-intake…</div>;
  return <Editor draft={draft} />;
}

/** What a pickup sentence says (AreaIntake's `pickupSentence`). */
export function pickup(i: IntakeResolved): string {
  return i.enabled
    ? `Every ${toMinutes(i.interval_s)} minutes, up to ${i.max_concurrent} at a time, starts ready beads at P${i.priority_ceiling} and below from ${i.repos.length ? i.repos.join(", ") : "every enabled repo"}. Each runs to its first gate and waits for you there.`
    : "Nothing is picked up automatically. Ready beads stay in the queue until someone files them.";
}

function Editor({ draft }: { draft: ConfigDraft }) {
  const r = draft.view!.result;
  const data = intakeOf(r);
  const [sel, setSel] = useState<Sel>("pickup");
  const [paneOpen, setPaneOpen] = useState(true);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [repos, setRepos] = useState<{ path: string; name?: string | null }[]>([]);
  const [chains, setChains] = useState<string[]>([]);

  useEffect(() => {
    let live = true;
    const load = () => void request<Check[]>("/intake/checks?limit=20").then((a) => { if (live && a.status === 200 && Array.isArray(a.body)) setChecks(a.body); });
    load();
    const t = setInterval(load, CHECKS_EVERY_MS);
    window.addEventListener("focus", load);
    return () => { live = false; clearInterval(t); window.removeEventListener("focus", load); };
  }, []);
  useEffect(() => {
    void request<{ repos: { path: string; name?: string | null }[] }>("/repos").then((a) => { if (a.status === 200) setRepos(a.body.repos ?? []); });
    void request<{ id: string }[]>("/templates/chains").then((a) => { if (a.status === 200 && Array.isArray(a.body)) setChains(a.body.map((c) => c.id)); });
  }, []);

  const changes = useMemo(() => new Map<string, Change>(r.changes.map((c) => [`${c.file ?? ""}|${c.path}`, c])), [r.changes]);
  const area = useMemo(() => ({
    crumb: "Auto-intake",
    files: ["intake.yaml", "policy.yaml"],
    toast: "Published auto-intake · applies now",
    affects: () => <Kv k="applies" v="without a restart: the poller is replaced and the new interval and schedules are live" />,
  }), []);

  const schedules = data?.schedules ?? [];
  const chosen = typeof sel === "number" ? schedules.find((s) => s.index === sel) : undefined;
  const onFix = (p: Problem) => {
    setPaneOpen(true);
    setSel(p.schedule !== undefined ? p.schedule : "pickup");
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
    if (a.status === 200) setSel("pickup");
  };

  return (
    <AreaFrame draft={draft} area={area} pageKey="intake" onFix={onFix}>
      {({ size, review, reserve }) => (
        <>
          <div className="ink-body" style={{ right: reserve(paneOpen) }}>
            <div className="ink-page">
              {!data ? (
                <div className="tpl-note" role="alert">
                  <h1>intake.yaml or policy.yaml does not load</h1>
                  {r.problems[0] && <p>{r.problems[0].message}</p>}
                  <p>Open YAML in the header to repair it.</p>
                </div>
              ) : (
                <>
                  <button type="button" className={`ink-card${sel === "pickup" ? " is-sel" : ""}${data.enabled ? " is-on" : ""}`} aria-pressed={sel === "pickup"} onClick={() => { setSel("pickup"); setPaneOpen(true); }}>
                    <span className="ink-status"><span className="ink-dot" aria-hidden /> {data.enabled ? "On" : "Off"}</span>
                    <span className="ink-sentence">{pickup(data)}</span>
                  </button>

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
                            {bad.map((p, i) => <p key={i} className="ink-err" role="alert">{p.message}</p>)}
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
          {!review && data && (
            <Inspector
              id="intake-pane"
              open={paneOpen}
              size={size}
              crumbs={[{ label: "Auto-intake", onClick: () => setSel("pickup") }]}
              icon={chosen ? "clock" : "download"}
              title={chosen ? chosen.title || "untitled" : "bd ready"}
              sub={chosen ? "schedule · triggers: in policy.yaml" : "pickup rule · intake.yaml"}
              onCollapse={() => setPaneOpen(false)}
              onExpand={() => setPaneOpen(true)}
              footer={chosen ? <><span className="bp-gap" /><Button variant="danger" onClick={() => void removeSchedule(chosen.index)}>Remove schedule</Button></> : undefined}
            >
              {chosen
                ? <ScheduleRows draft={draft} s={chosen} repos={repos} chains={chains} changes={changes} problems={problemsOfSchedule(r, chosen.index)} />
                : <PickupRows draft={draft} i={data!} repos={repos} changes={changes} />}
            </Inspector>
          )}
        </>
      )}
    </AreaFrame>
  );
}

const repoLabel = (path: string, repos: { path: string; name?: string | null }[]) => repos.find((r) => r.path === path)?.name || path.split("/").filter(Boolean).pop() || path || "no repo";

function Row({ k, children, note, changed }: { k: string; children: React.ReactNode; note?: string; changed?: boolean }) {
  return (
    <div className="ink-row">
      <span className="ink-k">{k}</span>
      <span className="ink-v">{children}</span>
      <span className={`ink-chip${changed ? " is-changed" : ""}`}>{changed ? "changed" : "this file"}</span>
      {note && <span className="ink-rownote">{note}</span>}
    </div>
  );
}

/** One `set_intake` or `set_schedule` write; the refusal's message or null. */
async function send(draft: ConfigDraft, op: Op): Promise<string | null> {
  const a = await draft.ops([op], { quiet: true });
  return a.status === 200 ? null : detailOf(a.body);
}

function PickupRows({ draft, i, repos, changes }: { draft: ConfigDraft; i: IntakeResolved; repos: { path: string; name?: string | null }[]; changes: Map<string, Change> }) {
  const set = (patch: Record<string, unknown>) => send(draft, { op: "set_intake", patch });
  const changed = (file: string, key: string) => changes.has(`${file}|${key}`);
  return (
    <div>
      <Row k="enabled" changed={changed("intake.yaml", "enabled")}>
        <Segmented label="enabled" value={i.enabled ? "yes" : "no"} options={[{ value: "yes", label: "yes" }, { value: "no", label: "no" }]} onChange={(v) => void set({ enabled: v === "yes" })} />
      </Row>
      <Row k="check every (min)" changed={changed("intake.yaml", "interval_s")}>
        <ValueCell label="check every, minutes" value={String(toMinutes(i.interval_s))} display={showMinutes(i.interval_s)} onCommit={(t) => { const v = intervalFromText(t); return "error" in v ? v.error : set({ interval_s: v.seconds }); }} />
      </Row>
      <Row k="at a time" changed={changed("policy.yaml", "max_concurrent")} note="Also Policy › Housekeeping: the same key.">
        <ValueCell label="at a time" value={String(i.max_concurrent)} display={String(i.max_concurrent)} onCommit={(t) => { const n = Number(t); return Number.isInteger(n) && n > 0 ? set({ max_concurrent: n }) : "Enter a whole number above 0."; }} />
      </Row>
      <Row k="priority at or below" changed={changed("intake.yaml", "priority_ceiling")}>
        <Segmented label="priority at or below" value={String(i.priority_ceiling)} options={PRIORITIES} onChange={(v) => void set({ priority_ceiling: Number(v) })} />
      </Row>
      <Row k="repos" changed={changed("intake.yaml", "repos")}>
        <ValueCell label="repos" value={i.repos.join(", ")} display={i.repos.length ? i.repos.join(", ") : "every enabled repo"} muted={!i.repos.length} placeholder={repos.map((r) => r.path).join(", ") || "every enabled repo"} onCommit={(t) => set({ repos: t.split(",").map((x) => x.trim()).filter(Boolean) })} />
      </Row>
    </div>
  );
}

function ScheduleRows({ draft, s, repos, chains, changes, problems }: { draft: ConfigDraft; s: Schedule; repos: { path: string; name?: string | null }[]; chains: string[]; changes: Map<string, Change>; problems: Problem[] }) {
  const patch = (p: Record<string, unknown>) => send(draft, { op: "set_schedule", index: s.index, patch: p });
  const changed = (field: string) => changes.has(`policy.yaml|triggers.${s.index}.${field}`);
  const text = (field: keyof Schedule, label: string, k = label) => (
    <Row k={k} changed={changed(field)}>
      <ValueCell label={label} value={String(s[field] ?? "")} display={String(s[field] ?? "") || "not set"} muted={!s[field]} onCommit={(t) => (t ? patch({ [field]: t }) : field === "description" ? patch({ description: "" }) : "This cannot be empty.")} />
      {problems.filter((p) => p.field === field).map((p, n) => <span key={n} className="ink-err" role="alert">{p.message}</span>)}
    </Row>
  );
  return (
    <div>
      {text("title", "name")}
      {text("cron", "cron")}
      <Row k="repo" changed={changed("repo")}>
        <select className="ink-select" aria-label="repo" value={s.repo} onChange={(e) => void patch({ repo: e.target.value })}>
          {[...new Set([s.repo, ...repos.map((r) => r.path)])].filter(Boolean).map((p) => <option key={p} value={p}>{repoLabel(p, repos)}</option>)}
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
