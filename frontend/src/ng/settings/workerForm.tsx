import { useEffect, useState } from "react";
import * as api from "../../api";
import { Field } from "../ui/Field";
import { PillInput } from "../ui/PillInput";
import { Segmented } from "../ui/Segmented";
import { Select } from "../ui/Select";
import { GIVE_UP_PRESETS } from "./workersWords";

/** The names of the connected repos, for the Repos pills (the list Settings › Repos shows). */
export function useRepoChoices() {
  const [names, setNames] = useState<string[]>([]);
  useEffect(() => {
    let live = true;
    api.getRepos().then((r) => { if (live) setNames((r.repos ?? []).map((x) => x.name)); }).catch(() => {});
    return () => { live = false; };
  }, []);
  return names.map((value) => ({ value }));
}

/** The number typed in Steps at once; undefined while it is blank. */
export function capacityValue(text: string): number | undefined {
  return text.trim() === "" ? undefined : Number(text);
}

export function CapacityField({ text, onChange, hint }: { text: string; onChange: (t: string) => void; hint: string }) {
  return (
    <Field label="Steps at once" hint={hint}>
      <input type="number" min={1} step={1} value={text} onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}

/** Give up after: a preset, or the value `workers.yaml` holds when it is none of them (shown as its own text, so nothing is lost). */
export function GiveUpField({ value, onChange, hint }: { value: string; onChange: (v: string) => void; hint: string }) {
  const listed = GIVE_UP_PRESETS.some((p) => p.value === value);
  return (
    <Field label="Give up after" hint={hint}>
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        {GIVE_UP_PRESETS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
        {!listed && <option value={value}>{value}</option>}
      </Select>
    </Field>
  );
}

export function GroupsField({ groups, onChange }: { groups: string[]; onChange: (g: string[]) => void }) {
  return (
    <div className="set-wk-field">
      <span className="field-label">Groups</span>
      <PillInput label="Groups" values={groups} onChange={onChange} placeholder="Add a group" placeholderMore="Add a group" empty="In no group. A step's runner can name a group." noun="group" />
    </div>
  );
}

export type RepoScope = "every" | "some";

export function ReposField({ scope, repos, choices, onScope, onRepos }: { scope: RepoScope; repos: string[]; choices: { value: string }[]; onScope: (s: RepoScope) => void; onRepos: (r: string[]) => void }) {
  return (
    <div className="set-wk-field">
      <span className="field-label">Repos</span>
      <Segmented label="Repos" value={scope} onChange={onScope} options={[{ value: "every", label: "Every repo" }, { value: "some", label: "Only some" }]} />
      {scope === "some" && <PillInput label="Repos" values={repos} onChange={onRepos} choices={choices} noun="connected repo" placeholder="Add a repo" placeholderMore="Add a repo" />}
    </div>
  );
}
