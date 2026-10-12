import { describe, expect, it } from "vitest";
import { ApiError, type ApplyItem, type ApplyReport } from "../../src/core/api";
import { decideApply, reloadProblems, resolveOffer, restartViaTerminal } from "../../src/core/apply";

const item = (o: Partial<ApplyItem>): ApplyItem => ({ id: "x", file: "", text: "t", ...o });
const report = (restart: Partial<ApplyItem>[], reload: Partial<ApplyItem>[], managed = false): ApplyReport => ({
  restart: restart.map(item),
  reload: reload.map(item),
  managed,
});
const startup = (file: string) => ({ kind: "restart", message: `Saved ${file}. Kraft reads it at startup. Restarting Kraft ends running agents.` });

describe("decideApply", () => {
  it.each([
    ["restart item for the file → restart", "access.yaml", report([{ file: "access.yaml", text: "port changes from 8765 to 9000" }], []),
      { kind: "restart", message: "Saved access.yaml. port changes from 8765 to 9000. Restarting Kraft ends running agents." }],
    ["two restart items are joined", "access.yaml", report([{ file: "access.yaml", text: "bind changes from 127.0.0.1 to 0.0.0.0" }, { file: "access.yaml", text: "port changes from 8765 to 9000" }], []),
      { kind: "restart", message: "Saved access.yaml. bind changes from 127.0.0.1 to 0.0.0.0; port changes from 8765 to 9000. Restarting Kraft ends running agents." }],
    ["reload item for the file → reload", "intake.yaml", report([], [{ file: "intake.yaml" }]), { kind: "reload", message: "Saved intake.yaml. Load it into the running Kraft?" }],
    ["chain file → reload", "chains/probe.yaml", report([], [{ file: "chains/probe.yaml" }]), { kind: "reload", message: "Saved chains/probe.yaml. Load it into the running Kraft?" }],
    ["ignores items for other files", "policy.yaml", report([{ file: "access.yaml", text: "port changes from 1 to 2" }], [{ file: "policy.yaml" }]), { kind: "reload", message: "Saved policy.yaml. Load it into the running Kraft?" }],
    ["restart wins over reload for the same file", "access.yaml", report([{ file: "access.yaml", text: "t" }], [{ file: "access.yaml" }]), { kind: "restart", message: "Saved access.yaml. t. Restarting Kraft ends running agents." }],
    ["nothing pending for the file → none", "harnesses.yaml", report([], [{ file: "policy.yaml" }]), { kind: "none" }],
    ["a reload item with a problem is not offered", "policy.yaml", report([], [{ file: "policy.yaml", problem: "bad" }]), { kind: "none" }],
  ])("%s", (_id, file, r, want) => expect(decideApply(file, r)).toEqual(want));

  // Files with no GET /apply item: read once at startup, or read per use.
  it.each([
    ["notify.yaml", startup("notify.yaml")],
    ["access.yaml", startup("access.yaml")],
    ["harnesses.yaml", { kind: "none" }],
    ["repos.yaml", { kind: "none" }],
    ["theme.yaml", { kind: "none" }],
    ["detectors.yaml", { kind: "none" }],
    ["sandbox.yaml", { kind: "none" }],
  ])("%s with nothing pending", (file, want) => expect(decideApply(file, report([], []))).toEqual(want));

  it("a notify.yaml pending for another file does not matter", () =>
    expect(decideApply("notify.yaml", report([], [{ file: "policy.yaml" }]))).toEqual(startup("notify.yaml")));
});

describe("resolveOffer", () => {
  it("a failed GET /apply is an error, never none", async () =>
    expect(await resolveOffer("policy.yaml", async () => { throw new ApiError(0, "down"); })).toEqual({ offer: { kind: "error" }, managed: false }));
  it("carries managed from the report", async () =>
    expect(await resolveOffer("policy.yaml", async () => report([], [{ file: "policy.yaml" }], true))).toMatchObject({ offer: { kind: "reload" }, managed: true }));
});

describe("reloadProblems", () => {
  it("lists every refusal, intake included", () =>
    expect(reloadProblems({ valid: [], invalid_templates: { probe: "no task x" }, refused_policy: "loops: bad", refused_intake: "schedules: bad" }))
      .toEqual(["probe: no task x", "policy.yaml: loops: bad", "intake.yaml: schedules: bad"]));
  it("is empty for a clean reload", () =>
    expect(reloadProblems({ valid: ["a"], invalid_templates: {}, refused_policy: null, refused_intake: null })).toEqual([]));
});

describe("restartViaTerminal", () => {
  it.each([[409, true], [500, true], [0, false], [401, false]])("ApiError %i → %s", (status, want) =>
    expect(restartViaTerminal(new ApiError(status, "x"))).toBe(want));
  it("a non-ApiError is not a fallback", () => expect(restartViaTerminal(new Error("x"))).toBe(false));
});
