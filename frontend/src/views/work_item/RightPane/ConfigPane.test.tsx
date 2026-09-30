import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { item } from "../../../testFixtures";
import type { WorkItemUsage } from "../../../types";
import { ConfigPane } from "./ConfigPane";

describe("ConfigPane budget", () => {
  it("marks the spend its cap counts as estimated when an agent reported no cost", () => {
    const usage = { total: { cost_complete: true, cost_estimated: true }, by_node: [] } as unknown as WorkItemUsage;
    render(<ConfigPane item={item({ budget_cap: { cap_usd: 10, source: "policy", spent_usd: 4.2 }, usage })} nodeId={null} />);
    expect(screen.getByText(/\$10\.00 · ~\$4\.20 \(est\.\) used/)).toBeInTheDocument();
  });
});
