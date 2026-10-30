import { describe, expect, it } from "vitest";
import { chainName } from "./chainName";

describe("chainName", () => {
  it("is the name the item was filed with, else the frozen chain's own id, else an unnamed chain", () => {
    expect(chainName({ chain_template: "quick-task", chain_definition: { template_id: "default" } })).toBe("quick-task");
    expect(chainName({ chain_template: null, chain_definition: { template_id: "default" } })).toBe("default");
    expect(chainName({ chain_template: null, chain_definition: null })).toBe("unnamed chain");
  });

  it("is \"one-off chain\" for a chain filed with one item only", () => {
    expect(chainName({ chain_template: "@one-off", chain_definition: { template_id: "one-off" } })).toBe("one-off chain");
  });
});
