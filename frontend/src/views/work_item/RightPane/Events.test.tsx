import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { KraftEvent } from "../../../types";
import { Events } from "./Events";

const findingsEvent = (): KraftEvent => ({
  seq: 1,
  work_item_id: "w1",
  type: "findings_measured",
  created_at: "2024-01-01T00:00:00Z",
  payload: {
    node_id: "verify",
    findings: [
      { severity: "minor", message: "unused import", file: "a.py", line: 3, source_plugin: "ruff" },
    ],
  },
});

describe("Events", () => {
  // Kraft-a4js: GateCard's one-liner only ever gives a count and links here
  // -- this is where the message/file/severity themselves must be readable.
  it("lists a findings_measured event's findings, not just the count", () => {
    render(
      <Events events={[findingsEvent()]} nodeId="verify" onViewLog={() => {}} />,
    );
    expect(screen.getByText(/1 findings measured/)).toBeInTheDocument();
    expect(screen.getByText(/unused import/)).toBeInTheDocument();
    expect(screen.getByText(/a\.py:3/)).toBeInTheDocument();
  });
});
