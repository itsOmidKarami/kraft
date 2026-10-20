import { DEFAULT_VIEW } from "./fixture.default";
import type { Result } from "./types";

/** `chains/company:ship`-style result of a child add-on's chain: node `implementation` runs a task
 *  the child overrides, `verification` one it adds. Tests only. */
export const INHERITED_NODES: NonNullable<NonNullable<Result["resolved"]>["inherited_nodes"]> = {
  implementation: { origin: "overridden", entry: "tasks.security_scan" },
  verification: { origin: "added", entry: "tasks.pci_review" },
};

/** The default chain's result with the child add-on's per-node marks; `marks` replaces them. */
export const inheritedResult = (marks: typeof INHERITED_NODES = INHERITED_NODES): Result => ({
  ...DEFAULT_VIEW.result,
  // The canvas under test is scoped to `team`, so the default chain's text is filed under it.
  model: { "chains/team.yaml": DEFAULT_VIEW.result.model["chains/default.yaml"] },
  resolved: { ...DEFAULT_VIEW.result.resolved!, inherited_nodes: marks },
});
