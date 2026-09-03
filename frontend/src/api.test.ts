import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: "x",
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
}

afterEach(() => vi.restoreAllMocks());

describe("api", () => {
  it("listWorkItems returns items + cursor", async () => {
    vi.stubGlobal("fetch", mockFetch(200, { items: [], cursor: 7 }));
    expect(await api.listWorkItems()).toEqual({ items: [], cursor: 7 });
  });

  it("createWorkItem posts JSON and returns the id", async () => {
    const f = mockFetch(201, { id: "abc" });
    vi.stubGlobal("fetch", f);
    const out = await api.createWorkItem({ repo: "/r", title: "t" });
    expect(out.id).toBe("abc");
    expect(f).toHaveBeenCalledWith(
      "/work-items",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("throws with the server detail on non-2xx", async () => {
    vi.stubGlobal("fetch", mockFetch(422, { detail: "bad repo" }));
    await expect(api.createWorkItem({ repo: "/nope", title: "t" })).rejects.toThrow(
      "bad repo",
    );
  });

  it("rejectGate sends the note", async () => {
    const f = mockFetch(200, {});
    vi.stubGlobal("fetch", f);
    await api.rejectGate("id1", "spec_approval", "redo");
    expect(f).toHaveBeenCalledWith(
      "/work-items/id1/gates/spec_approval/reject",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ note: "redo" }),
      }),
    );
  });

  it("logUrl builds the log path", () => {
    expect(api.logUrl("s9")).toBe("/worker-sessions/s9/log");
  });
});
