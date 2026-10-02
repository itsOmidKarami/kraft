import { PillInput } from "../ui/PillInput";
import { Inspector } from "../graph/Inspector";
import type { useResizable } from "../graph/useResizable";
import { Head, Note, SelectRow } from "../templates/panes/controls";
import type { ConfigDraft } from "../templates/draft/useConfigDraft";
import { ACCESS_WORD, ESCALATION_PATH, type HProblem, type Resolved } from "./model";
import type { Published } from "./usePublishedPolicy";
import { problemText } from "../templates/problems";

export type AreaTab = "overview" | "config";
const TABS: { value: AreaTab; label: string }[] = [{ value: "overview", label: "Overview" }, { value: "config", label: "Config" }];

/** The area pane (nothing selected): Overview (problems, counts) and Config (allowed tools, escalation). */
export function AreaPane({ draft, r, problems, published, tab, onTab, open, size, onCollapse, onExpand, onProblem, tabs = TABS, children }: {
  draft: ConfigDraft;
  r: Resolved;
  problems: HProblem[];
  published: Published | null;
  tab: string;
  onTab: (t: string) => void;
  open: boolean;
  size: ReturnType<typeof useResizable>;
  onCollapse: () => void;
  onExpand: () => void;
  /** Select what a problem is about. */
  onProblem: (p: HProblem) => void;
  tabs?: { value: string; label: string }[];
  /** A tab this component does not draw (the YAML view). */
  children?: React.ReactNode;
}) {
  const available = r.harnesses.filter((h) => h.state === "available").length;
  const tasks = new Set(r.harnesses.flatMap((h) => h.tasks.filter((t) => !t.fallback).map((t) => `${t.chain}/${t.path}`))).size;
  const profiles = Object.keys(r.profiles).length;

  const send = (op: Record<string, unknown>) => void draft.ops([{ op: "set_escalation", harness: r.escalation.harness, grants: r.escalation.grants, ...op }]);
  const effective = r.escalation_effective.harness;
  const runsOn = [
    { value: "item", label: "item's harness" },
    ...r.harnesses.filter((h) => h.state !== "never" || h.id === effective).map((h) => ({ value: h.id, label: h.state === "never" ? `${h.id} (Never)` : h.id })),
  ];
  const escalationBad = problems.some((p) => p.path === ESCALATION_PATH);
  const first = problems[0];

  return (
    <Inspector
      id="harnesses-area"
      open={open}
      size={size}
      crumbs={[{ label: "Harnesses" }]}
      icon="bot"
      title="harnesses"
      sub="harnesses.yaml · access, tools and escalation from policy.yaml"
      prob={first ? { msg: problemText(first), fix: problems.length > 1 ? `and ${problems.length - 1} more` : first.fix } : undefined}
      tabs={tabs}
      tab={tab}
      onTab={onTab}
      onCollapse={onCollapse}
      onExpand={onExpand}
    >
      {tab === "overview" && (
        <>
          <Head>Problems · {problems.length}</Head>
          {problems.length === 0 && <Note>Every task can run where it is set to.</Note>}
          <ul className="hn-problems-list">
            {problems.map((p, i) => (
              <li key={i}>
                <button type="button" className="hn-problem" onClick={() => onProblem(p)}>
                  <span className="hn-problem-mark" aria-hidden>!</span>
                  <span>{problemText(p)}</span>
                </button>
              </li>
            ))}
          </ul>
          <Head>Counts</Head>
          <Note>{r.harnesses.length} harnesses, {available} available to tasks. {profiles} profiles. {tasks} agent tasks.</Note>
          <Note>Access: {r.harnesses.map((h) => `${h.id} ${ACCESS_WORD[h.state].toLowerCase()}`).join(", ")}.</Note>
        </>
      )}
      {tab === "config" && (
        <>
          <Head>Tools</Head>
          <PillInput
            label="allowed tools"
            values={r.allowed_tools ?? []}
            added={published ? (r.allowed_tools ?? []).filter((t) => !(published.tools ?? []).includes(t)) : []}
            empty="Empty allows no tools. Remove the list in YAML to allow every tool."
            onChange={(tools) => void draft.ops([{ op: "set_allowed_tools", tools }])}
          />
          <Note>The built-in tools an agent may use. Layers below can only narrow it; a repo adds deny tools in Repos. A harness that can't enforce a list refuses to launch under one.</Note>
          <Head>Escalation</Head>
          <SelectRow
            label="runs on"
            value={r.escalation.harness ?? effective}
            options={runsOn}
            bad={escalationBad}
            sub={escalationBad ? "This harness is set to Never, so escalation cannot run there." : "\"item's harness\" follows the one the item's latest agent task used. Harnesses set to Never aren't offered."}
            onPick={(harness) => send({ harness })}
          />
          <Head>Grants</Head>
          <PillInput
            label="escalation grants"
            values={r.escalation.grants ?? []}
            added={published ? (r.escalation.grants ?? []).filter((g) => !(published.grants ?? []).includes(g)) : []}
            empty="Escalation turns get only their node's own grants."
            choices={draft.view?.result.choices?.grants}
            noun="grant"
            onChange={(grants) => send({ grants })}
          />
          <Note>What every escalation turn may do on top of its node's own grants.</Note>
        </>
      )}
      {children}
    </Inspector>
  );
}
