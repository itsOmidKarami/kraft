import type { ProbeCandidate } from "../../../types/settings";

/** What a probe read its proposal from: the sources of the root's chosen
 *  `role` commands ("justfile recipe `test`"), or null when it chose none. */
export function chosenSource(candidates: ProbeCandidate[] | undefined, role: "test" | "setup"): string | null {
  const chosen = (candidates ?? []).filter((c) => c.chosen && c.role === role && c.dir === "");
  return chosen.length ? chosen.map((c) => c.source).join(" + ") : null;
}

/** How many commands the probe found and did not propose: `kraft repo connect`
 *  lists them, and Templates › Repos is where a person picks one instead. */
export function otherCount(candidates: ProbeCandidate[] | undefined): number {
  return (candidates ?? []).filter((c) => !c.chosen).length;
}

/** `cmd — from source`, or the command alone when the probe gave no source. */
export function withSource(command: string, source: string | null): string {
  return source ? `${command} — from ${source}` : command;
}
