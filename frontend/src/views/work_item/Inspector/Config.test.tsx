import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChainNode, WorkItem } from "../../../types";
import { Config } from "./Config";

const NODES: ChainNode[] = [
  {
    id: "verify",
    tasks: ["on.test.run", "on.review.local.run"],
    steps: [["on.test.run"], ["on.review.local.run"]],
    gate_after: null,
  },
];

const item = {
  id: "w1", title: "T", repo: "/r", status: "active", chain_template: "default",
  chain_definition: { template_id: "default", nodes: NODES },
  effective_chain: { template_id: "default", nodes: NODES },
  current_node_id: "verify", bead_id: "B", created_at: "t", updated_at: "t",
} as unknown as WorkItem;

describe("Config tab · a stepped node (Kraft-1y9ae)", () => {
  it("shows the groups in order instead of one comma list", () => {
    render(<Config item={item} nodeId="verify" />);
    const panel = screen.getByTestId("inspector-config");
    expect(panel.textContent).toContain("on.test.run → on.review.local.run");
    expect(panel.textContent).not.toContain("on.test.run, on.review.local.run");
  });
});
