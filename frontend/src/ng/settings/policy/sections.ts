export type Section = "limits" | "loops" | "housekeeping";
export const SECTIONS: { id: Section; label: string; sub: string; icon: string }[] = [
  { id: "limits", label: "Limits", sub: "time and spend caps by scope", icon: "gauge" },
  { id: "loops", label: "Loops", sub: "fix-loop defaults, findings, stuck items", icon: "refresh-cw" },
  { id: "housekeeping", label: "Housekeeping", sub: "concurrency, relaunches, archive, forge", icon: "archive" },
];

/** The section a `policy.yaml` key belongs to (Decisions §12: Limits, Loops, Housekeeping). */
export function sectionOfKey(key: string): Section | null {
  if (/^(budget\.|(defaults|maxima)\.(work_item|nodes|steps|tasks)\.)/.test(key)) return "limits";
  if (/^((defaults|maxima)\.(max_attempts|timeout_minutes)|loops\.|default\.|findings\.|auto_escalate_|auto_review_attempts)/.test(key)) return "loops";
  if (/^(max_concurrent|archive\.|storage\.|rate_limit_retries|forge_cli_timeout_s)/.test(key)) return "housekeeping";
  return null;
}

/** The section a problem is about: its key when it names one, else its group (`scope`). */
export function sectionOfProblem(p: { scope?: string; field?: string | null; path?: string }): Section | null {
  const byKey = sectionOfKey(p.field || p.path || "");
  if (byKey) return byKey;
  if (p.scope === "limits") return "limits";
  if (p.scope === "loops" || p.scope === "escalation") return "loops";
  if (p.scope === "housekeeping" || p.scope === "retries") return "housekeeping";
  return null;
}

/** Which of a fix loop's numbers wins, said once under the attempts and wall-clock blocks on Loops (docsite "Fix loop and judge" › Limits). */
export const LOOP_WINS = "The most specific wins: an item's own policy, then the loop's own keys, then its node, then its entry below (else the row for loops without one).";
