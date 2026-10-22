import type { Choice } from "../ui/Combobox";
import type { Harnesses } from "../../types";
import type { ProviderStatus } from "../harnesses/useProviders";
import { authoredNodes, kindOf, resolvedNode } from "./draft/view";
import type { Result, Scope } from "./draft/types";
import { harnessChoices, steeringChoices } from "./choices";
import { effortsFor } from "./panes/useHarnessOptions";
import type { ChoiceSource, FieldMeta } from "./fields";
import type { useLibrary } from "./useLibrary";
import type { useHarnessOptions } from "./panes/useHarnessOptions";

/** What the desktop Config tab and the phone's setting sheets both offer for a field: the same sets, in one place. */
export type At = { r: Result; scope: Scope; path: string };

/** "reject to" goes back to an exec node before this gate. */
export function earlierExec({ r, scope, path }: At): string[] {
  const nodes = authoredNodes(r, scope);
  const i = nodes.findIndex((n) => n.id === path);
  return nodes.slice(0, Math.max(0, i)).filter((n) => kindOf(r, n) === "exec").map((n) => n.id);
}

/** "on base change" restarts from this node or an earlier exec node. */
export function restartTargets({ r, scope, path }: At): string[] {
  const nodes = authoredNodes(r, scope);
  const i = nodes.findIndex((n) => n.id === path);
  return nodes.slice(0, i + 1).filter((n) => kindOf(r, n) === "exec").map((n) => n.id);
}

/** "on merge rejected" restarts from an exec node before this one, and before the chain's first final gate when
 *  that comes first: the repaired head goes back through the checks and the final review (the server's
 *  `merge_rejection_limit`). */
export function rejectRestartTargets({ r, scope, path }: At): string[] {
  const nodes = authoredNodes(r, scope);
  const i = nodes.findIndex((n) => n.id === path);
  const final = nodes.findIndex((n) => kindOf(r, n) === "gate" && !!(resolvedNode(r, n.id)?.chain_finalized ?? n.chain_finalized));
  const limit = final >= 0 && final < i ? final : Math.max(0, i);
  return nodes.slice(0, limit).filter((n) => kindOf(r, n) === "exec").map((n) => n.id);
}

/** What one value of each closed set is, for its refusal. */
export const NOUN: Record<ChoiceSource, string> = {
  ref: "action",
  target: "target",
  inputs: "input",
  grants: "grant",
  steering: "steering profile",
  harnesses: "harness",
  documents: "document produced before this gate",
  earlier: "exec node before this gate",
};

/** The closed set a typed field takes its values from, or null when it has none. */
export function choicesFor(meta: FieldMeta, at: At, h: Harnesses | null, library: ReturnType<typeof useLibrary>): Choice[] | null {
  const plain = (vs: string[]) => vs.map((value) => ({ value }));
  switch (meta.choices) {
    case "ref":
    case "target":
    case "inputs":
    case "grants":
      return at.r.choices?.[meta.choices] ?? null;
    case "steering":
      return steeringChoices(library);
    case "harnesses":
      return harnessChoices(h);
    case "documents":
      return plain(at.r.resolved?.documents[at.path] ?? []);
    case "earlier":
      return plain(earlierExec(at));
    default:
      return null;
  }
}

/** The values an enum field picks from; null while the harness lists load or when the field has none. */
export function optionsFor(meta: FieldMeta, at: At, opts: ReturnType<typeof useHarnessOptions>): string[] | null {
  const loaded = typeof opts === "string" ? null : opts;
  const harness = String(at.r.sources[at.path]?.harness?.value ?? "claude");
  return meta.options === "harness" ? loaded?.harnesses.profiles.map((p) => p.id) ?? null
    : meta.options === "profile" ? loaded?.harnesses.agent_profiles.map((p) => p.id) ?? null
    : meta.options === "effort" ? effortsFor(opts, harness)
    : meta.options === "restart" ? restartTargets(at)
    : meta.options === "rejectRestart" ? rejectRestartTargets(at)
    : meta.options ?? null;
}

/** The models a harness's provider is known to take, to suggest (any other may still be typed). */
export function modelsFor(listed: ProviderStatus[], h: Harnesses | null, harness: string): Choice[] {
  const provider = h?.profiles.find((p) => p.id === harness)?.provider ?? harness;
  return (listed.find((p) => p.id === provider)?.models ?? []).map((value) => ({ value }));
}
