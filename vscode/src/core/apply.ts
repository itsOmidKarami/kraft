import { ApiError, type ApplyReport, type ReloadReport } from "./api";

export type ApplyOffer = { kind: "restart"; message: string } | { kind: "reload"; message: string } | { kind: "none" } | { kind: "error" };

// Files the server reads once at startup and does not track in `GET /apply`:
// `notify.yaml` (only PUT /notify rereads it) and `access.yaml`'s other keys.
// Every other config file without an item is read per use, or refreshed by doctor.
const STARTUP_READ: ReadonlySet<string> = new Set(["notify.yaml", "access.yaml"]);

const restartMessage = (file: string, what: string) => `Saved ${file}. ${what}. Restarting Kraft ends running agents.`;

export function decideApply(file: string, report: ApplyReport): ApplyOffer {
  const restart = report.restart.filter((i) => i.file === file);
  if (restart.length) return { kind: "restart", message: restartMessage(file, restart.map((i) => i.text).join("; ")) };
  const reload = report.reload.filter((i) => i.file === file);
  if (reload.some((i) => !i.problem)) return { kind: "reload", message: `Saved ${file}. Load it into the running Kraft?` };
  if (STARTUP_READ.has(file)) return { kind: "restart", message: restartMessage(file, "Kraft reads it at startup") };
  return { kind: "none" };
}

// A failed `GET /apply` is no verdict, not "nothing pending".
export async function resolveOffer(file: string, pending: () => Promise<ApplyReport>): Promise<{ offer: ApplyOffer; managed: boolean }> {
  try {
    const report = await pending();
    return { offer: decideApply(file, report), managed: report.managed };
  } catch {
    return { offer: { kind: "error" }, managed: false };
  }
}

export function reloadProblems(r: ReloadReport): string[] {
  return [
    ...Object.entries(r.invalid_templates).map(([chain, reason]) => `${chain}: ${reason}`),
    ...(r.refused_policy ? [`policy.yaml: ${r.refused_policy}`] : []),
    ...(r.refused_intake ? [`intake.yaml: ${r.refused_intake}`] : []),
  ];
}

// The server answers 409 for a Kraft started from a terminal and 500 when the restart would not start.
export const restartViaTerminal = (e: unknown): boolean => e instanceof ApiError && (e.status === 409 || e.status === 500);

// A Kraft a service manager started restarts itself; one started from a terminal is restarted there.
export const restartRoute = (managed: boolean): "api" | "terminal" => (managed ? "api" : "terminal");
