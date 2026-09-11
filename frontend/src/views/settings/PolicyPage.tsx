import { useState } from "react";
import * as api from "../../api";
import { SectionLabel } from "../../components/ui";
import type { Policy } from "../../types";
import { PageHead, SaveRow, useResource } from "./shared";

/* ── 5d policy ────────────────────────────────────────────────────────────── */

export function PolicyPage() {
  const { value, error, reload } = useResource(() => api.getPolicy());
  const [draft, setDraft] = useState<Policy | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const policy = draft ?? value;
  const dirty = draft !== null;

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

  return (
    <>
      <PageHead
        title="Policy"
        note="every loop stops at attempts or wall-clock, whichever comes first; a spend budget stops the next agent task the same way — the item comes back to you"
      />
      {error && <p className="form-error">{error}</p>}
      <div className="cap-row cap-head">
        <span>Counter</span>
        <span>Attempts</span>
        <span>Wall-clock (s)</span>
      </div>
      {rows.map(([key, cap]) => (
        <div key={key} className="cap-row" data-loop={key}>
          <span className="hook-name">{key}</span>
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
            aria-label={`${key} wall clock`}
            value={cap.wall_clock_s}
            onChange={(e) => setCap(key, "wall_clock_s", Number(e.target.value))}
          />
        </div>
      ))}
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
