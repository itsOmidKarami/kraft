import { expect, it, vi } from "vitest";
import { approveGate } from "../../src/core/approve";

it("sends the artifact's digest when approving a chain revision", async () => {
  const api = { getArtifact: vi.fn(async () => ({ digest: "d1" })), approve: vi.fn(async () => ({})) } as any;
  await approveGate(api, "A", "revise");
  expect(api.approve).toHaveBeenCalledWith("A", "revise", "d1");
});

it("approves without a digest when the gate has no artifact", async () => {
  const api = { getArtifact: vi.fn(async () => null), approve: vi.fn(async () => ({})) } as any;
  await approveGate(api, "A", "review");
  expect(api.approve).toHaveBeenCalledWith("A", "review", undefined);
});
