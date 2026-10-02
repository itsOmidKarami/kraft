import type { ProbeCandidate } from "../../../types/settings";

/** What a probe read its proposal from: the sources of the root's chosen
 *  `role` commands ("justfile recipe `test`"), or null when it chose none. */
export function chosenSource(candidates: ProbeCandidate[] | undefined, role: "test" | "setup"): string | null {
  const chosen = (candidates ?? []).filter((c) => c.chosen && c.role === role && c.dir === "");
  return chosen.length ? chosen.map((c) => c.source).join(" + ") : null;
}

/** The commands the probe found and did not propose, the first `shown` of them
 *  named with their source: "make test (Makefile target `test`) and 2 more".
 *  Said here, since once the repo is connected nothing lists them again. */
export function others(candidates: ProbeCandidate[] | undefined, shown = 3): string | null {
  const rest = (candidates ?? []).filter((c) => !c.chosen);
  if (!rest.length) return null;
  const named = rest.slice(0, shown).map((c) => `${c.dir ? `${c.dir}/: ` : ""}${c.command} (${c.source})`);
  return named.join(", ") + (rest.length > shown ? ` and ${rest.length - shown} more` : "");
}

/** The commit the probe read, as a person names it: `origin/main`, or the
 *  checkout's HEAD when the clone has no origin branch. Edits not committed
 *  and pushed there are not read, so it is said. */
export function readFrom(ref: string | null | undefined): string | null {
  if (!ref) return null;
  const named = ref.replace(/^refs\/remotes\//, "");
  return named === "HEAD"
    ? "this checkout's HEAD (no origin branch): uncommitted edits are not read"
    : `${named}, where work items start: commits not pushed there are not read`;
}

/** `cmd — from source`, or the command alone when the probe gave no source. */
export function withSource(command: string, source: string | null): string {
  return source ? `${command} — from ${source}` : command;
}
