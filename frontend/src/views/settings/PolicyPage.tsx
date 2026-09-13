import { useState } from "react";
import { Check } from "@phosphor-icons/react";
import * as api from "../../api";
import { SectionLabel, Switch } from "../../components/ui";
import { useStore } from "../../store";
import type { Policy, TemplateSummary } from "../../types";
import { PageHead, PhoneHeader, SaveRow, usePhone, useResource } from "./shared";

/* ── 5d policy (design 29, phone m10 right) ──────────────────────────────── */

const SEVERITIES = ["critical", "important", "minor", "info"] as const;

/** Template ids with a node keyed to this loop cap — `fix_loop` for a
 *  regular loop, `<gate>_reject_loop` for a gate's reject loop
 *  (`executor.gates.reject_target`) — same "used by" idiom PluginsPage's
 *  `templatesUsingHook` reads for hooks. */
function templatesUsingLoop(templates: TemplateSummary[], key: string) {
  return templates
    .filter((t) =>
      t.nodes.some((n) => n.fix_loop === key || (n.gate_after && `${n.gate_after}_reject_loop` === key)),
    )
    .map((t) => t.id);
}

export function PolicyPage() {
  const { value, error, reload } = useResource(() => api.getPolicy());
  const { value: templatesValue } = useResource(() => api.getTemplates());
  const templates = templatesValue ?? [];
  const [draft, setDraft] = useState<Policy | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const phone = usePhone();
  const policy = draft ?? value;
  const dirty = draft !== null;
  const activeCount = useStore((s) => Object.values(s.workItems).filter((i) => i.status === "active").length);

  const setCap = (key: string, field: "attempts" | "wall_clock_s", n: number) => {
    if (!policy) return;
    if (key === "default") {
      setDraft({ ...policy, default: { ...policy.default, [field]: n } });
      return;
    }
    setDraft({
      ...policy,
      loops: { ...policy.loops, [key]: { ...policy.loops[key], [field]: n } },
    });
  };

  const setBudget = (field: "work_item_usd" | "daily_usd", raw: string) => {
    if (!policy) return;
    // "" is the operator clearing the cap, which is null — not 0, which would
    // block every launch.
    const n = raw.trim() === "" ? null : Number(raw);
    setDraft({
      ...policy,
      budget: { work_item_usd: null, daily_usd: null, ...policy.budget, [field]: n },
    });
  };

  const setArchiveAfterDays = (raw: string) => {
    if (!policy) return;
    // "" clears the cap -- null, same as a budget field, not 0 (which would
    // archive every item on its next poll).
    const n = raw.trim() === "" ? null : Number(raw);
    setDraft({ ...policy, archive: { after_days: n } });
  };

  const addLoop = () => {
    if (!policy) return;
    const name = window.prompt("Loop key (the node's own `fix_loop`, e.g. `verify_fix_loop`)");
    if (!name || policy.loops[name]) return;
    setDraft({ ...policy, loops: { ...policy.loops, [name]: { ...policy.default } } });
  };

  const toggleSeverity = (sev: string) => {
    if (!policy) return;
    const current = policy.findings?.loop_severities ?? ["critical", "important"];
    const next = current.includes(sev) ? current.filter((s) => s !== sev) : [...current, sev];
    setDraft({ ...policy, findings: { ...policy.findings, loop_severities: next } });
  };

  const setAutoEscalateStuck = (next: boolean) => {
    if (!policy) return;
    setDraft({ ...policy, auto_escalate_stuck: next });
  };

  const setAutoEscalateStuckCap = (n: number) => {
    if (!policy) return;
    setDraft({ ...policy, auto_escalate_stuck_cap: n });
  };

  const setAutoEscalateDelay = (n: number) => {
    if (!policy) return;
    setDraft({ ...policy, auto_escalate_delay_s: n });
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.putPolicy(draft);
      setDraft(null);
      await reload();
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const rows = policy
    ? [...Object.entries(policy.loops), ["default", policy.default] as const]
    : [];
  const selectedSeverities = policy?.findings?.loop_severities ?? ["critical", "important"];

  const body = (
    <>
      {error && <p className="form-error">{error}</p>}
      <SectionLabel>Loop caps</SectionLabel>
      <div className="cap-row cap-head">
        <span>Counter</span>
        <span>Attempts</span>
        <span>Wall-clock (min)</span>
        <span>Used by</span>
      </div>
      {rows.map(([key, cap]) => (
        <div key={key} className="cap-row" data-loop={key}>
          <span className="hook-name">
            {key}
            {key === "default" && (
              <span className="field-hint"> · any loop not listed above</span>
            )}
          </span>
          <input
            className="input"
            type="number"
            min={1}
            aria-label={`${key} attempts`}
            value={cap.attempts}
            onChange={(e) => setCap(key, "attempts", Number(e.target.value))}
          />
          <input
            className="input"
            type="number"
            min={1}
            step="0.1"
            aria-label={`${key} wall clock`}
            value={Math.round((cap.wall_clock_s / 60) * 10) / 10}
            onChange={(e) => setCap(key, "wall_clock_s", Math.round(Number(e.target.value) * 60))}
          />
          <span className="row-sub">
            {key === "default" ? "—" : templatesUsingLoop(templates, key).join(", ") || "no template"}
          </span>
        </div>
      ))}
      <div className="save-row">
        <button type="button" className="btn btn-ghost" onClick={addLoop}>
          + Add loop
        </button>
      </div>

      <SectionLabel>Concurrency</SectionLabel>
      <p className="settings-note">
        Counts every active item, however it was started. Starting past the limit is refused
        with "all slots are busy"; the auto-intake poller only fills free slots.
      </p>
      <div className="cap-row budget-row" data-cap="max_concurrent">
        <span className="hook-name">Max active work items</span>
        <input
          className="input"
          type="number"
          min={1}
          aria-label="max concurrent work items"
          value={policy?.max_concurrent ?? ""}
          onChange={(e) => policy && setDraft({ ...policy, max_concurrent: Number(e.target.value) })}
        />
      </div>
      {policy && (
        <p className="settings-note">
          {activeCount} of {policy.max_concurrent} slots in use
        </p>
      )}

      <SectionLabel>Budget</SectionLabel>
      <div className="cap-row budget-row" data-budget="work_item_usd">
        <span className="hook-name">Per work item ($)</span>
        <input
          className="input"
          type="number"
          min={0}
          step="0.01"
          aria-label="work item budget"
          value={policy?.budget?.work_item_usd ?? ""}
          onChange={(e) => setBudget("work_item_usd", e.target.value)}
        />
      </div>
      <div className="cap-row budget-row" data-budget="daily_usd">
        <span className="hook-name">Per day ($)</span>
        <input
          className="input"
          type="number"
          min={0}
          step="0.01"
          aria-label="daily budget"
          value={policy?.budget?.daily_usd ?? ""}
          onChange={(e) => setBudget("daily_usd", e.target.value)}
        />
      </div>
      <p className="settings-note">
        Blank is no cap. A cap refuses to start the next agent task; it cannot stop
        one already running, because an agent only reports its cost when its session
        ends. Expect to overshoot by up to the cost of one task.
      </p>

      <SectionLabel>Archive</SectionLabel>
      <div className="cap-row budget-row" data-cap="archive_after_days">
        <span className="hook-name">Auto-archive completed/abandoned items after (days)</span>
        <input
          className="input"
          type="number"
          min={0}
          aria-label="archive after days"
          value={policy?.archive?.after_days ?? ""}
          onChange={(e) => setArchiveAfterDays(e.target.value)}
        />
      </div>
      <p className="settings-note">
        Blank never auto-archives. `archive_poller` only ever archives a completed or abandoned
        item — a running one is untouched regardless of age.
      </p>

      <SectionLabel>Findings that burn a fix cycle</SectionLabel>
      <p className="settings-note">
        Unselected severities are recorded and shown at the human_review gate instead.
      </p>
      <div className="submodules">
        {SEVERITIES.map((sev) => (
          <button
            key={sev}
            type="button"
            className={`tag ${selectedSeverities.includes(sev) ? "" : "tag-off"}`}
            aria-pressed={selectedSeverities.includes(sev)}
            onClick={() => toggleSeverity(sev)}
          >
            {selectedSeverities.includes(sev) && <Check size={11} />} {sev}
          </button>
        ))}
      </div>

      <SectionLabel>Rate limits</SectionLabel>
      <div className="cap-row budget-row" data-cap="rate_limit_retries">
        <span className="hook-name">Auto-relaunch after a rejected API rate limit</span>
        <input
          className="input"
          type="number"
          min={1}
          aria-label="rate limit retries"
          value={policy?.rate_limit_retries ?? ""}
          onChange={(e) =>
            policy && setDraft({ ...policy, rate_limit_retries: Number(e.target.value) })
          }
        />
      </div>

      <SectionLabel>Auto-escalate on stuck</SectionLabel>
      <p className="settings-note">
        Org-wide default for a work item that reports no progress: whether it escalates
        to a human on its own, how many times before giving up, and how long it waits
        first. A chain template or per-item override can still turn this off or retune it
        for one node.
      </p>
      <div className="cap-row budget-row" data-cap="auto_escalate_stuck">
        <span className="hook-name">Escalate a stuck item automatically</span>
        <Switch
          checked={policy?.auto_escalate_stuck ?? true}
          onChange={setAutoEscalateStuck}
          label="auto-escalate on stuck"
        />
      </div>
      <div className="cap-row budget-row" data-cap="auto_escalate_stuck_cap">
        <span className="hook-name">Max auto-escalations per item</span>
        <input
          className="input"
          type="number"
          min={1}
          aria-label="auto-escalate stuck cap"
          value={policy?.auto_escalate_stuck_cap ?? 3}
          onChange={(e) => setAutoEscalateStuckCap(Number(e.target.value))}
        />
      </div>
      <div className="cap-row budget-row" data-cap="auto_escalate_delay_s">
        <span className="hook-name">Delay before escalating (s)</span>
        <input
          className="input"
          type="number"
          min={0}
          aria-label="auto-escalate delay"
          value={policy?.auto_escalate_delay_s ?? 0}
          onChange={(e) => setAutoEscalateDelay(Number(e.target.value))}
        />
      </div>
    </>
  );

  if (phone) {
    return (
      <>
        <PhoneHeader
          back="Settings"
          backTo="/settings"
          title="Policy"
          action={
            <button className="btn btn-primary" disabled={busy || !dirty} onClick={save}>
              Save
            </button>
          }
        />
        {body}
      </>
    );
  }

  return (
    <>
      <PageHead
        title="Policy"
        note="every loop stops at attempts or wall-clock, whichever comes first; a spend budget stops the next agent task the same way — the item comes back to you"
      />
      {body}
      <SaveRow
        onSave={save}
        onDiscard={() => setDraft(null)}
        dirty={dirty}
        busy={busy}
        message={message}
        hint="writes policy.yaml · applies to loops that start after the save"
      />
    </>
  );
}
