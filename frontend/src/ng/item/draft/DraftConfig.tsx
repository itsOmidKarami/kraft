import { useHarnessOptions } from "../../templates/panes/useHarnessOptions";
import { Head, Note } from "../../templates/panes/controls";
import { useProviders } from "../../harnesses/useProviders";
import { capAt, capText, effortOf, effortOptions, materialized, modelOf, modelSuggestions, notStarted, providerOf, taskAt, type Given, type Materialized } from "../chainValues";
import { OverrideRow } from "../panes/OverrideRow";
import { show } from "../../templates/fields";
import { useRepoEntry } from "../useRepoEntry";
import { useDraft } from "./context";
import { fieldsFor, type DraftField } from "./fields";
import type { Op } from "./types";
import { overrideOf, setField } from "./view";
import type { Harnesses, Repo } from "../../../types";
import type { ItemDetail } from "../useItem";

/** The Config rows ✎ makes overrides on, for a node, step or task that has not
 *  run (Decisions §5 Editing the chain). On an item that has not started, each
 *  row shows what the chain gives, read from the chain it froze at intake, and
 *  a task offers only the fields its kind has. Once it has started a row says
 *  "as the chain gives it" until it is overridden, and an editor starts empty
 *  (Decided 7). */
export function DraftConfig({ path, saying }: { path: string; saying?: string }) {
  const d = useDraft();
  const opts = useHarnessOptions();
  const listed = useProviders();
  const repo = useRepoEntry(d?.raw.repo ?? "");
  if (!d || d.draft.status !== "ready") return null;
  const node = path.split(".")[0];
  if (!d.editable(node)) return <Note>{saying ?? "Already run or running: edit a later node."}</Note>;
  const chain = notStarted(d.raw) ? materialized(d.raw) : null;
  const h = typeof opts === "string" ? null : opts.harnesses;
  const task = chain ? taskAt(chain, path) : undefined;
  const fields = fieldsFor(path).filter((f) => !task || applies(f, task.kind));
  return (
    <>
      <Head>Override for this item</Head>
      <div className="cfg">{fields.map((f) => <Row key={`${f.group}.${f.key}`} f={f} path={path} chain={chain} h={h} listed={listed} given={chain ? wider(d.raw, path, f) ?? given(chain, d.raw, path, f, h, repo) : null} />)}</div>
      <Note>Saved as a draft, not applied yet: Review &amp; apply applies these{chain ? ", and Start asks about them first" : ""}.</Note>
      {path.split(".").length === 3 && <Note>The run reads an override when it reaches this task.{!task || task.kind === "agent" ? " A prompt override replaces the whole prompt for this item." : ""}</Note>}
    </>
  );
}

/** The task fields a kind has: an agent's model and prompt, a subprocess's command; a cap applies to any. */
const applies = (f: DraftField, kind: string) =>
  f.group === "policy" || (kind === "agent" ? f.key !== "command" : kind === "subprocess" ? f.key === "command" : false);

/** A task's model or effort set for the item over the chain's: by its node,
 *  else item-wide. Either wins over the task's own when it runs. */
function wider(item: ItemDetail, path: string, f: DraftField): Given | null {
  if (f.key !== "model" && f.key !== "effort") return null;
  const key = f.key;
  const node = item.node_overrides?.[path.split(".")[0]]?.[key];
  if (node) return { value: node, source: "node" };
  const all = item.agent_overrides?.[key];
  return all ? { value: all, source: "item-wide" } : null;
}

/** What applies to a field at `path` while this item's draft sets nothing, and where it comes from; null when that is not known. */
function given(chain: Materialized, item: ItemDetail, path: string, f: DraftField, h: Harnesses | null, repo: Repo | null): Given | null {
  if (f.group === "policy") {
    const c = capAt(chain, path, f.key, item.policy_override);
    return { value: capText(f.key, c.value), source: c.source };
  }
  const t = taskAt(chain, path);
  if (!t) return null;
  if (f.key === "model") return modelOf(t, h, repo);
  if (f.key === "effort") return effortOf(t, h);
  if (f.key === "command") return { value: show(t.command, "list"), source: "chain" };
  const v = (t as Record<string, unknown>)[f.key];
  return typeof v === "string" && v ? { value: v, source: "chain" } : null;
}

function Row({ f, path, chain, h, listed, given }: { f: DraftField; path: string; chain: Materialized | null; h: Harnesses | null; listed: ReturnType<typeof useProviders>; given: Given | null }) {
  const d = useDraft()!;
  const opts = useHarnessOptions();
  const set = overrideOf(d.ops, path)?.[f.group]?.[f.key];
  const problem = d.issues.find((i) => i.path === path && !i.passed && opHolds(d.ops[i.index], f));
  const task = chain ? taskAt(chain, path) : undefined;
  const providers = task ? [providerOf(h, task.harness)] : [];
  const options = f.key === "harness" && typeof opts !== "string"
    ? opts.harnesses.profiles.map((p) => p.id)
    : f.key === "effort"
      ? task ? effortOptions(providers, listed) : [...new Set(typeof opts === "string" ? [] : Object.values(opts.providers.valid).flatMap((p) => p.capabilities?.effort?.values ?? []))]
      : null;
  return (
    <OverrideRow
      label={f.label}
      hint={task ? undefined : f.hint}
      kind={f.kind}
      own={set}
      given={given}
      options={options}
      suggest={f.key === "model" && task ? modelSuggestions(providers, listed, h) : undefined}
      problem={problem?.message}
      scope="draft"
      placeholder={f.key === "prompt" ? "Replaces the whole prompt for this item" : undefined}
      onSave={(value) => void d.draft.edit((ops) => setField(ops, path, f.group, f.key, value), path)}
      onReset={() => void d.draft.edit((ops) => setField(ops, path, f.group, f.key, undefined), path)}
    />
  );
}

const opHolds = (op: Op | undefined, f: DraftField) => op?.op === "override" && op[f.group]?.[f.key] !== undefined;
