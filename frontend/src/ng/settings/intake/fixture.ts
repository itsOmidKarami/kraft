import type { DraftView, Result } from "../../templates/draft/types";
import type { IntakeResolved } from "./types";

export const INTAKE: IntakeResolved = {
  enabled: true,
  interval_s: 300,
  priority_ceiling: 2,
  repos: [],
  schedules: [{ index: 0, cron: "0 9 * * 1-5", repo: "/src/platform", chain: "default", title: "Dependency check", description: "Update pinned dependencies." }],
};

export function intakeView(over: Partial<Result> = {}, resolved: IntakeResolved | null = INTAKE, draft = false): DraftView {
  return {
    area: "intake",
    key: "intake",
    draft,
    files: { "intake.yaml": "enabled: true\n" },
    base: { "intake.yaml": "x" },
    published: { "intake.yaml": "enabled: true\n" },
    updated_at: null,
    result: { model: {}, problems: [], sources: {}, changes: [], warnings: [], policy_values: { auto_escalate_delay_s: 0, auto_review_attempts: 1 }, impact: null as never, resolved: resolved as never, ...over },
  };
}
