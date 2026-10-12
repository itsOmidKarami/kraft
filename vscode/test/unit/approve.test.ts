import { beforeEach, expect, it, vi, type Mock } from "vitest";
import { ApiError } from "../../src/core/api";
import { approveGate, approveOrRead, isStaleRevision } from "../../src/core/approve";
import { ShownRenders } from "../../src/core/shown";

const withDigest = { title: "t", content: "c", truncated: false, path: "p", digest: "d2" };
let api: any;
let shown: ShownRenders;
let show: Mock<(a: any) => Promise<void>>;
beforeEach(() => {
  api = { getArtifact: vi.fn(async () => withDigest), approve: vi.fn(async () => ({})) };
  shown = new ShownRenders();
  show = vi.fn(async (_a: any) => {});
});

it("approveGate sends the digest it is given and never fetches", async () => {
  await approveGate(api, "A", "revise", "d1");
  expect(api.getArtifact).not.toHaveBeenCalled();
  expect(api.approve).toHaveBeenCalledWith("A", "revise", "d1");
});

it("never fetches when a render was shown", async () => {
  shown.record("A", "revise", "d1");
  expect(await approveOrRead(api, shown, "A", "revise", show)).toBe("approved");
  expect(api.getArtifact).not.toHaveBeenCalled();
  expect(api.approve).toHaveBeenCalledWith("A", "revise", "d1");
});

it("fetches and shows when nothing was shown and the artifact has a digest", async () => {
  expect(await approveOrRead(api, shown, "A", "revise", show)).toBe("read-first");
  expect(show).toHaveBeenCalledWith(expect.objectContaining({ digest: "d2" }));
  expect(api.approve).not.toHaveBeenCalled();
});

it("a second call approves with the recorded digest without fetching again", async () => {
  await approveOrRead(api, shown, "A", "revise", show);
  expect(await approveOrRead(api, shown, "A", "revise", show)).toBe("approved");
  expect(api.getArtifact).toHaveBeenCalledTimes(1);
  expect(api.approve).toHaveBeenCalledWith("A", "revise", "d2");
});

it("a throwing show records nothing", async () => {
  show.mockRejectedValue(new Error("no tab"));
  await expect(approveOrRead(api, shown, "A", "revise", show)).rejects.toThrow("no tab");
  expect(shown.get("A", "revise")).toBeUndefined();
});

it("asks to confirm only when the approve will be sent", async () => {
  const confirm = vi.fn(async () => true);
  expect(await approveOrRead(api, shown, "A", "revise", show, confirm)).toBe("read-first");
  expect(confirm).not.toHaveBeenCalled();
  expect(await approveOrRead(api, shown, "A", "revise", show, confirm)).toBe("approved");
  expect(confirm).toHaveBeenCalledTimes(1);
});

it("a declined confirm approves nothing", async () => {
  shown.record("A", "revise", "d1");
  expect(await approveOrRead(api, shown, "A", "revise", show, async () => false)).toBe("cancelled");
  expect(api.approve).not.toHaveBeenCalled();
});

it.each([
  { id: "no artifact", artifact: null },
  { id: "artifact without digest", artifact: { title: "t", content: "", truncated: false, path: "p" } },
])("approves as today when $id", async ({ artifact }) => {
  api.getArtifact.mockResolvedValue(artifact);
  expect(await approveOrRead(api, shown, "A", "spec", show)).toBe("approved");
  expect(show).not.toHaveBeenCalled();
  expect(api.approve).toHaveBeenCalledWith("A", "spec", undefined);
});

it.each([
  { id: "revision changed", e: new ApiError(409, "revise: the revision changed since you viewed it; review it again"), stale: true },
  { id: "needs the digest", e: new ApiError(409, "revise: approving a chain revision needs the digest of the one you reviewed; read it ..."), stale: true },
  { id: "other 409", e: new ApiError(409, "already approved"), stale: false },
  { id: "422 same words", e: new ApiError(422, "the revision changed since you viewed it"), stale: false },
  { id: "not an ApiError", e: new Error("the revision changed since you viewed it"), stale: false },
])("isStaleRevision: $id", ({ e, stale }) => expect(isStaleRevision(e)).toBe(stale));
