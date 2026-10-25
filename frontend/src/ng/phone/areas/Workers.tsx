import { useEffect, useState } from "react";
import { ago, plural } from "../../../format";
import type { Machine } from "../../../types";
import { detailOf, request } from "../../http";
import { ADD_COMMAND, EVERY_REPO, NO_MACHINES, READ_ONLY_NOTE, giveUpWords, isLocal, modeWords, slotsWords, stateReason, stateWord } from "../../settings/workersWords";
import { AreaScreen } from "./AreaScreen";
import { Group, type RowSpec } from "./kit";

/** `/settings/workers`: the machines that run work item steps, read-only (adding one is the terminal's job). */
export function WorkersScreen() {
  const [machines, setMachines] = useState<Machine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void request<{ workers: Machine[] }>("/workers").then((r) => {
      if (r.status !== 200) return setError(detailOf(r.body));
      setMachines(r.body.workers);
    });
  }, []);
  const local = machines?.find(isLocal);
  const others = machines?.filter((m) => !isLocal(m));
  const localRow: RowSpec[] = local
    ? [{ key: local.name, label: local.name, mono: true, value: stateWord(local), sub: `${modeWords(local)}${stateReason(local) ? `, ${stateReason(local)}` : ""} · ${slotsWords(local)}${local.kraft_version ? ` · ${local.kraft_version}` : ""}` }]
    : [];
  const rows = (others ?? []).map((m): RowSpec => ({
    key: m.name,
    label: m.name,
    mono: true,
    value: stateWord(m),
    sub: `${modeWords(m)}${stateReason(m) ? `, ${stateReason(m)}` : ""} · ${slotsWords(m)} slots · ${m.repos?.length ? m.repos.join(", ") : EVERY_REPO} · ${giveUpWords(m.give_up_after)}${m.last_seen ? ` · seen ${ago(m.last_seen)}` : ""}`,
  }));
  return (
    <AreaScreen title="Workers" sub="The machines that run work item steps, and whether each is reachable." yaml={false}>
      {error && <p className="ph-error" role="alert">{error}</p>}
      {local && <Group title="This machine" rows={localRow} />}
      {others && (
        <Group
          title={plural(others.length, "machine")}
          rows={rows}
          foot={others.length ? READ_ONLY_NOTE : `${NO_MACHINES} ${ADD_COMMAND}`}
        />
      )}
    </AreaScreen>
  );
}
