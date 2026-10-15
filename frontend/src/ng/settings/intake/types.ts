import type { Problem, Result } from "../../templates/draft/types";
import { cronProblem } from "./cron";

export interface Schedule {
  index: number;
  cron: string;
  repo: string;
  chain: string;
  title: string;
  description: string;
}

/** `result.resolved` of an `intake` draft (docsite "Intake ops"). */
export interface IntakeResolved {
  enabled: boolean;
  interval_s: number;
  priority_ceiling: number;
  repos: string[];
  schedules: Schedule[];
}

export const intakeOf = (r: Result): IntakeResolved | null => {
  const v = r.resolved as unknown as Partial<IntakeResolved> | null;
  return v && typeof v.interval_s === "number" && Array.isArray(v.schedules) ? (v as IntakeResolved) : null;
};

/** One row of `GET /intake/checks`. */
export interface Check {
  id: number | string;
  at: string;
  ready: number;
  started: string[];
  skipped: { bead_id: string; reason: string }[];
}

export const problemsOfSchedule = (r: Result, index: number): Problem[] => r.problems.filter((p) => p.schedule === index);

/** What a schedule's problem says: a cron the scheduler refuses is explained in words, not in the server's. */
export const scheduleProblemText = (p: Problem, s: Schedule): string => (p.field === "cron" ? cronProblem(s.cron) ?? p.message : p.message);
