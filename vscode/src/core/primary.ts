import { rowAction } from "../../../frontend/src/ng/board/rowText";
import type { ItemAction, WorkItem } from "./api";

/** A row's one inline button, from the web board's own `rowAction`: the
 *  labels it returns are matched here and nowhere else. */
export type Primary = "openArtifact" | "answer" | "retry" | "raiseBudget" | "resume" | "openInWebUi";

/** The ones that change the item: hidden while Kraft is read-only. */
export const MUTATING_PRIMARY: ReadonlySet<Primary> = new Set<Primary>(["answer", "retry", "raiseBudget", "resume"]);

export const PRIMARY_COMMAND: Record<Primary, string> = {
  openArtifact: "kraft.openArtifact",
  answer: "kraft.answer",
  retry: "kraft.retry",
  raiseBudget: "kraft.raiseBudget",
  resume: "kraft.resume",
  openInWebUi: "kraft.openInWebUi",
};

/** The row token its inline entry keys off: `primaryOpenArtifact`, and so on. */
export const primaryToken = (p: Primary) => `primary${p[0].toUpperCase()}${p.slice(1)}`;

/** A door the button would go through only while the server admits it. */
const when = (item: WorkItem, door: ItemAction, p: Primary): Primary | null => (item.actions?.includes(door) ? p : null);

/** The row's primary action, or null when it offers none or the server would
 *  refuse it now. An answer resumes the item, never retries it. A peek the
 *  extension has no editor for ("Raise cap", "Open") opens the web UI. */
export function primaryAction(item: WorkItem): Primary | null {
  const a = rowAction(item);
  if (!a) return null;
  if (a.kind === "gate") return "openArtifact";
  if (a.kind === "resume") return when(item, "resume", "resume");
  switch (a.label) {
    case "Answer": return when(item, "resume", "answer");
    case "Raise budget": return when(item, "raise_budget", "raiseBudget");
    case "Retry…": return when(item, "retry", "retry");
    default: return "openInWebUi";
  }
}

/** A row's click (D32): only a gate row has one, and it opens the gate
 *  document. Any other row only selects; Open in Web UI stays a button. */
export const rowCommand = (item: WorkItem): { command: string } | undefined =>
  primaryAction(item) === "openArtifact" ? { command: PRIMARY_COMMAND.openArtifact } : undefined;

/** What Open Item… opens: its gate document while a gate waits, else the item in the web UI. */
export const clickTarget = (item: WorkItem): "openArtifact" | "openInWebUi" =>
  primaryAction(item) === "openArtifact" ? "openArtifact" : "openInWebUi";
