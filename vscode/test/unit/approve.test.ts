import { beforeEach, expect, it, vi, type Mock } from "vitest";
import { ApiError } from "../../src/core/api";
import { approvedMessage, approveGate, approveOrRead, gateConsequence, isStaleRevision } from "../../src/core/approve";
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

it.each([
  { id: "digest remembered", digest: "d1", artifact: withDigest },
  { id: "no digest", digest: undefined, artifact: null },
])("a declined confirm approves nothing: $id", async ({ digest, artifact }) => {
  if (digest) shown.record("A", "revise", digest);
  api.getArtifact.mockResolvedValue(artifact);
  expect(await approveOrRead(api, shown, "A", "revise", show, async () => false)).toBe("cancelled");
  expect(api.approve).not.toHaveBeenCalled();
});

it("keeps the drafts when the approve fails and drops them when it lands", async () => {
  const approved = vi.fn(async () => {});
  shown.record("A", "revise", "d1");
  api.approve.mockRejectedValueOnce(new ApiError(409, "the revision changed since you viewed it"));
  await expect(approveOrRead(api, shown, "A", "revise", show, async () => true, approved)).rejects.toThrow();
  expect(approved).not.toHaveBeenCalled();
  await approveOrRead(api, shown, "A", "revise", show, async () => true, approved);
  expect(approved).toHaveBeenCalledTimes(1);
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

it("uses the given send with the remembered digest", async () => {
  const send = vi.fn(async (_d: string | undefined) => true);
  shown.record("A", "revise", "d1");
  expect(await approveOrRead(api, shown, "A", "revise", show, undefined, undefined, send)).toBe("approved");
  expect(send).toHaveBeenCalledWith("d1");
  expect(api.approve).not.toHaveBeenCalled();
});

it("returns cancelled when send declines, and runs no approved", async () => {
  const approved = vi.fn(async () => {});
  shown.record("A", "revise", "d1");
  expect(await approveOrRead(api, shown, "A", "revise", show, undefined, approved, async () => false)).toBe("cancelled");
  expect(approved).not.toHaveBeenCalled();
});

it("read-first still opens the document before any send", async () => {
  const send = vi.fn(async (_d: string | undefined) => true);
  expect(await approveOrRead(api, shown, "A", "revise", show, undefined, undefined, send)).toBe("read-first");
  expect(show).toHaveBeenCalled();
  expect(send).not.toHaveBeenCalled();
});

it.each<[string, string, { current_node_id: string | null; display_status?: string; pending_gate?: string | null } | null, string]>([
  ["no item to read", "spec_approval", null, "Approved: T · approve spec"],
  ["still at the gate", "spec_approval", { current_node_id: "plan", pending_gate: "spec_approval" }, "Approved: T · approve spec"],
  ["moved to the next node", "spec_approval", { current_node_id: "plan" }, "Approved: T · approve spec · now plan"],
  ["done", "final_review", { current_node_id: null, display_status: "done" }, "Approved: T · approve final review · done"],
])("approvedMessage: %s", (_, gate, after, want) => expect(approvedMessage("T", gate, after as never)).toBe(want));

const node = (id: string, kind: "exec" | "gate", tasks: string[] = []) => ({ id, kind, tasks, gate_after: kind === "gate" ? id : null });
it.each<[string, ReturnType<typeof node>[], string, "merge" | "push" | null]>([
  ["a merge node after the gate", [node("final_review", "gate"), node("mark_ready", "exec", ["ready"]), node("merge", "exec", ["merge"])], "final_review", "merge"],
  ["a push task after the gate", [node("ship_gate", "gate"), node("publish", "exec", ["publish/push"])], "ship_gate", "push"],
  ["the next gate comes first", [node("spec_approval", "gate"), node("plan", "exec"), node("plan_approval", "gate"), node("merge", "exec")], "spec_approval", null],
  ["no such gate", [node("merge", "exec")], "nope", null],
])("gateConsequence: %s", (_, nodes, gate, want) =>
  expect(gateConsequence({ chain_definition: { template_id: "t", nodes } } as never, gate)).toBe(want));
