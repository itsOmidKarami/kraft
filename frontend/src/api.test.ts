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

  it("search builds the query string and omits blank filters", async () => {
    const f = mockFetch(200, { query: "x", mode: "fts", results: [] });
    vi.stubGlobal("fetch", f);
    await api.search({ q: "reconnect backoff", kind: "specs", repo: "", source_kind: "" });
    const url = f.mock.calls[0][0] as string;
    expect(url).toContain("/search?");
    expect(url).toContain("q=reconnect+backoff");
    expect(url).toContain("kind=specs");
    expect(url).not.toContain("repo=");
    expect(url).not.toContain("source_kind=");
  });

  it("getDocument fetches by id and returns the row", async () => {
    vi.stubGlobal("fetch", mockFetch(200, { id: "d1", content: "# hi", metadata: {} }));
    const doc = await api.getDocument("d1");
    expect(doc.id).toBe("d1");
  });

  it("search surfaces the 422 detail from a bad FTS query", async () => {
    vi.stubGlobal("fetch", mockFetch(422, { detail: 'bad search query: near "x"' }));
    await expect(api.search({ q: '"x' })).rejects.toThrow(/bad search query/);
  });
});
