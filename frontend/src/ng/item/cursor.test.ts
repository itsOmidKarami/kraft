import { describe, expect, it } from "vitest";
import { findNode, nextNode } from "./cursor";
import { SLICED } from "./testkit";

describe("chain cursor", () => {
  it.each([["slices", "slices"], ["slices.verification", "slices.verification"], ["verification", undefined], ["ghost", undefined], [null, undefined]])("finds %s, a node inside a group by its path", (id, want) => {
    expect(findNode(SLICED, id)?.id).toBe(want);
  });

  // "then" on a node's pane: inside a group the next body node, and after the group's last the node after the group.
  it.each([["plan_approval", "slices"], ["slices", "local_review"], ["slices.implementation", "slices.verification"], ["slices.verification", "local_review"], ["merge_request", undefined]])("goes from %s to %s", (id, want) => {
    expect(nextNode(SLICED, id)?.id).toBe(want);
  });
});
