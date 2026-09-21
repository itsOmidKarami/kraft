import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChainNode } from "../../../types";
import { Config } from "./Config";
import { item as baseItem } from "../../../testFixtures";

const NODES: ChainNode[] = [
  {
    id: "verify",
    tasks: ["on.test.run", "on.review.local.run"],
    steps: [["on.test.run"], ["on.review.local.run"]],
    gate_after: null,
  },
];

const item = baseItem({ chain_definition: { template_id: "default", nodes: NODES }, effective_chain: { template_id: "default", nodes: NODES } });

describe("Config tab · a stepped node (Kraft-1y9ae)", () => {
  it("shows the groups in order instead of one comma list", () => {
    render(<Config item={item} nodeId="verify" />);
    const panel = screen.getByTestId("inspector-config");
    expect(panel.textContent).toContain("on.test.run → on.review.local.run");
    expect(panel.textContent).not.toContain("on.test.run, on.review.local.run");
  });
});
