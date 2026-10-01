import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { detailOf, request } from "../../http";
import { subscribeLive } from "../../live";
import { checkText, hhmm } from "../../settings/intake/checks";
import { describeCron } from "../../settings/intake/cron";
import { intakeOf, problemsOfSchedule, type Check, type IntakeResolved, type Schedule } from "../../settings/intake/types";
import { intervalFromText, PRIORITIES, showMinutes, toMinutes } from "../../settings/intake/units";
import type { Op } from "../../templates/draft/types";
import { useConfigDraft, type ConfigDraft } from "../../templates/draft/useConfigDraft";
import { showToast } from "../../ui/Toast";
import { ConfirmSheet, useSheet } from "../nav/Sheet";
import { AreaScreen } from "./AreaScreen";
import { Group, useEditor, type RowSpec } from "./kit";

const CHECKS_EVERY_MS = 30_000;
const AREA = "/settings/auto-intake";
type NamedRepo = { path: string; name?: string | null };
const repoLabel = (path: string, repos: NamedRepo[]) => repos.find((r) => r.path === path)?.name || path.split("/").filter(Boolean).pop() || path || "no repo";

/** The pickup rule in one line (the prototype's status sentence). */
export function statusLine(i: IntakeResolved): string {
  return i.enabled
    ? `On · checking every ${showMinutes(i.interval_s)}, ${i.max_concurrent} at a time, ${i.repos.length ? i.repos.join(", ") : "every enabled repo"}, P${i.priority_ceiling} or above.`
    : "Off · nothing is picked up automatically. Ready beads stay in the queue until someone files them.";
}

const send = async (draft: ConfigDraft, op: Op) => {
  const a = await draft.ops([op], { quiet: true });
  return a.status === 200 ? null : detailOf(a.body);
};

/** The repos Kraft knows and the chains it has, for the pickers; read once. */
function useChoices() {
  const [repos, setRepos] = useState<NamedRepo[]>([]);
  const [chains, setChains] = useState<string[]>([]);
  useEffect(() => {
    let live = true;
    void request<{ repos: NamedRepo[] }>("/repos").then((a) => live && a.status === 200 && setRepos(a.body.repos ?? []));
    void request<{ id: string }[]>("/templates/chains").then((a) => live && a.status === 200 && Array.isArray(a.body) && setChains(a.body.map((c) => c.id)));
    return () => void (live = false);
  }, []);
  return { repos, chains };
}

/** Recent checks: the list the desktop reads, a live `intake_checked` frame prepended, and a refetch every 30 s and on focus for a frame that never came. */
function useChecks() {
  const [checks, setChecks] = useState<Check[] | null>(null);
  useEffect(() => subscribeLive("intake_checked", (payload) => {
    const c = payload as Check;
    if (c && typeof c === "object" && "ready" in c) setChecks((prev) => [c, ...(prev ?? []).filter((x) => x.id !== c.id)].slice(0, 20));
  }), []);
  useEffect(() => {
    let live = true;
    const load = () => void request<Check[]>("/intake/checks?limit=20").then((a) => live && a.status === 200 && Array.isArray(a.body) && setChecks(a.body));
    load();
    const t = setInterval(load, CHECKS_EVERY_MS);
    window.addEventListener("focus", load);
    return () => { live = false; clearInterval(t); window.removeEventListener("focus", load); };
  }, []);
  return checks;
}

/** `/settings/auto-intake`: the pickup rule, recent checks and the schedules (W17 brief N.2). */
export function IntakeScreen() {
  const draft = useConfigDraft("intake", "intake");
  const navigate = useNavigate();
  const { edit, node } = useEditor();
  const { repos, chains } = useChoices();
  const checks = useChecks();
  const r = draft.view?.result;
  const data = r ? intakeOf(r) : null;
  const changed = (file: string, key: string) => !!r?.changes.some((c) => c.path === key && (c.file ?? file) === file);
  const set = (patch: Record<string, unknown>) => send(draft, { op: "set_intake", patch });

  const addSchedule = async () => {
    const a = await draft.ops([{ op: "add_schedule", cron: "0 9 * * 1", repo: repos[0]?.path ?? "", chain: chains.includes("default") ? "default" : chains[0] ?? "default", title: "Scheduled item" }], { quiet: true });
    if (a.status !== 200) return showToast(detailOf(a.body));
    const now = intakeOf((a.body as unknown as { result: NonNullable<typeof r> }).result);
    navigate(`${AREA}/schedules/${(now?.schedules.length ?? 1) - 1}`);
  };

  return (
    <AreaScreen title="Auto-intake" sub="Picks up ready beads on a timer, and files scheduled items." draft={draft}>
      {draft.status === "error" && !draft.view && <p className="ph-error" role="alert">Auto-intake could not be read.</p>}
      {r && !data && <p className="ph-error" role="alert">intake.yaml or policy.yaml does not load{r.problems[0] ? `: ${r.problems[0].message}` : ""}. Open YAML to repair it.</p>}
      {data && (
        <>
          <p className={`ph-intake-status${data.enabled ? " ph-is-on" : ""}`}>{statusLine(data)}</p>
          <Group title="Pickup" rows={[
            { label: "enabled", sw: data.enabled, changed: changed("intake.yaml", "enabled"), onSwitch: (on) => void set({ enabled: on }).then((e) => e && showToast(e)) },
            { label: "check every", value: showMinutes(data.interval_s), changed: changed("intake.yaml", "interval_s"), onEdit: () => edit({ kind: "text", title: "Check every", help: "In minutes. Not more often than every 30 seconds.", value: String(toMinutes(data.interval_s)), set: async (t) => { const v = intervalFromText(t); return "error" in v ? v.error : set({ interval_s: v.seconds }); } }) },
            { label: "at a time", value: String(data.max_concurrent), sub: "Also Policy › Housekeeping: the same key.", changed: changed("policy.yaml", "max_concurrent"), onEdit: () => edit({ kind: "text", title: "At a time", help: "How many items auto-intake keeps running at once.", value: String(data.max_concurrent), set: async (t) => { const n = Number(t); return Number.isInteger(n) && n > 0 ? set({ max_concurrent: n }) : "Enter a whole number above 0."; } }) },
            { label: "priority at or below", value: `P${data.priority_ceiling}`, changed: changed("intake.yaml", "priority_ceiling"), onEdit: () => edit({ kind: "choice", title: "Priority at or below", value: String(data.priority_ceiling), options: PRIORITIES, set: (v) => set({ priority_ceiling: Number(v) }) }) },
            { label: "repos", value: data.repos.length ? data.repos.join(", ") : "every enabled repo", changed: changed("intake.yaml", "repos"), onEdit: () => edit({ kind: "text", title: "Repos", help: "Comma separated paths. Empty means every enabled repo.", value: data.repos.join(", "), placeholder: repos.map((x) => x.path).join(", ") || "every enabled repo", set: (t) => set({ repos: t.split(",").map((x) => x.trim()).filter(Boolean) }) }) },
          ]} />
          <Group
            title="Recent checks"
            note={!data.enabled ? "Auto-intake is off." : checks === null ? "Loading…" : checks.length === 0 ? "No checks yet." : undefined}
            rows={data.enabled && checks ? checks.map((c): RowSpec => ({ key: String(c.id), label: checkText(c, data.priority_ceiling), value: hhmm(c.at), mono: false })) : []}
          />
          <Group
            title="Schedules"
            add={{ label: "Add schedule", run: () => void addSchedule() }}
            note={data.schedules.length === 0 ? "No schedules yet." : undefined}
            foot="Auto-intake removes the typing, not the judgement: a started item passes no gate by itself. Applies without a restart once published."
            rows={data.schedules.map((s): RowSpec => ({
              key: String(s.index), label: s.title || "untitled", to: `${AREA}/schedules/${s.index}`,
              sub: `${describeCron(s.cron)} · ${repoLabel(s.repo, repos)} · ${s.chain} · filed paused`,
              chips: problemsOfSchedule(r!, s.index).length ? [{ label: "problem", tone: "bad" }] : undefined,
            }))}
          />
        </>
      )}
      {node}
    </AreaScreen>
  );
}

/** `/settings/auto-intake/schedules/:index`: one schedule's fields, and Remove schedule. */
export function ScheduleScreen() {
  const { index: param } = useParams();
  const draft = useConfigDraft("intake", "intake");
  const { edit, node } = useEditor();
  const sheet = useSheet();
  const { repos, chains } = useChoices();
  const [error, setError] = useState<string | null>(null);
  const r = draft.view?.result;
  const data = r ? intakeOf(r) : null;
  const s: Schedule | undefined = data?.schedules.find((x) => String(x.index) === param);
  const own = s && r ? problemsOfSchedule(r, s.index) : [];
  const changed = (field: string) => !!s && !!r?.changes.some((c) => c.path === `triggers.${s.index}.${field}`);
  const patch = (p: Record<string, unknown>) => send(draft, { op: "set_schedule", index: s!.index, patch: p });
  const bad = (field: string) => own.find((p) => p.field === field)?.message;

  const textRow = (field: "title" | "cron" | "description", label: string, o: { multiline?: boolean; sub?: string } = {}): RowSpec => ({
    key: field, label, value: s![field] || "not set", mono: field === "cron", changed: changed(field),
    sub: bad(field) ?? o.sub, chips: bad(field) ? [{ label: "problem", tone: "bad" }] : undefined,
    onEdit: () => edit({ kind: "text", title: label, value: s![field], multiline: o.multiline, set: async (t) => (t || field === "description" ? patch({ [field]: t }) : "This cannot be empty.") }),
  });
  const pickRow = (field: "repo" | "chain", label: string, values: string[], show: (v: string) => string): RowSpec => ({
    key: field, label, value: show(s![field]), mono: true, changed: changed(field), sub: bad(field), chips: bad(field) ? [{ label: "problem", tone: "bad" }] : undefined,
    onEdit: () => edit({ kind: "choice", title: label, value: s![field], options: [...new Set([s![field], ...values])].filter(Boolean).map((v) => ({ value: v, label: show(v) })), set: (v) => patch({ [field]: v }) }),
  });

  const remove = async () => {
    setError(null);
    const a = await draft.ops([{ op: "remove_schedule", index: s!.index }], { quiet: true });
    if (a.status !== 200) return setError(detailOf(a.body));
    sheet.goTo(AREA);
    showToast("Schedule removed in the draft. Publish to apply it.");
  };

  return (
    <AreaScreen title={s ? s.title || "untitled" : "Schedule"} sub={s ? `${describeCron(s.cron)} · files an item paused` : undefined} draft={draft}>
      {data && !s && <p className="ph-empty">There is no schedule {param}.</p>}
      {s && (
        <>
          <Group rows={[
            textRow("title", "name"),
            textRow("cron", "cron", { sub: describeCron(s.cron) }),
            pickRow("repo", "repo", repos.map((x) => x.path), (v) => repoLabel(v, repos)),
            pickRow("chain", "chain", chains, (v) => v),
            textRow("description", "description", { multiline: true }),
            { label: "filed paused", value: "always", sub: "A scheduled item waits for you to resume it; no agent starts work from a timer." },
          ]} />
          <Group rows={[{ label: "Remove schedule", danger: true, onClick: () => { setError(null); sheet.open("remove"); } }]} />
        </>
      )}
      {node}
      {sheet.is("remove") && s && (
        <ConfirmSheet title={`Remove ${s.title || "this schedule"}?`} text="It stops filing items once you publish. Items it already filed are left as they are." error={error} confirm={{ label: "Remove schedule", danger: true, run: () => void remove() }} onClose={sheet.close} />
      )}
    </AreaScreen>
  );
}
