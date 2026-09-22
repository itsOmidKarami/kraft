// @vitest-environment node
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
  it("listWorkItems GETs /work-items with include_abandoned and returns items + cursor", async () => {
    const f = mockFetch(200, { items: [], cursor: 7 });
    vi.stubGlobal("fetch", f);
    expect(await api.listWorkItems()).toEqual({ items: [], cursor: 7 });
    expect(f).toHaveBeenCalledWith(
      "/api/work-items?include_abandoned=true",
      expect.anything(),
    );
  });

  it("createWorkItem posts JSON and returns the id", async () => {
    const f = mockFetch(201, { id: "abc" });
    vi.stubGlobal("fetch", f);
    const out = await api.createWorkItem({ repo: "/r", title: "t" });
    expect(out.id).toBe("abc");
    expect(f).toHaveBeenCalledWith(
      "/api/work-items",
      expect.objectContaining({ method: "POST" }),
    );
    const init = f.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({ repo: "/r", title: "t" });
    expect(init.headers).toMatchObject({ "content-type": "application/json" });
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
      "/api/work-items/id1/gates/spec_approval/reject",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ note: "redo" }),
      }),
    );
  });

  it("encodes custom gate names in gate action URLs", async () => {
    const f = mockFetch(200, {});
    vi.stubGlobal("fetch", f);
    await api.approveGate("id1", "release/ready#1");
    expect(f).toHaveBeenCalledWith(
      "/api/work-items/id1/gates/release%2Fready%231/approve",
      expect.anything(),
    );
  });

  it("approveGate sends a chain revision's digest back, and no body otherwise", async () => {
    const f = mockFetch(200, {});
    vi.stubGlobal("fetch", f);
    await api.approveGate("id1", "chain_revision_approval", "d1");
    await api.approveGate("id1", "spec_approval");
    expect(f.mock.calls[0][1]).toEqual(expect.objectContaining({ method: "POST", body: JSON.stringify({ digest: "d1" }) }));
    expect(f.mock.calls[1][1].body).toBeUndefined();
  });

  it("logUrl builds the log path", () => {
    expect(api.logUrl("s9")).toBe("/worker-sessions/s9/log");
  });

  it("search builds the query string and omits blank filters", async () => {
    const f = mockFetch(200, { query: "x", mode: "fts", results: [] });
    vi.stubGlobal("fetch", f);
    await api.search({ q: "reconnect backoff", kind: "specs", repo: "", source_kind: "" });
    const url = f.mock.calls[0][0] as string;
    expect(url).toContain("/api/search?");
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

  it("getWorkItemDocuments fetches the work item's documents", async () => {
    const f = mockFetch(200, { work_item_id: "w1", documents: [] });
    vi.stubGlobal("fetch", f);
    const body = await api.getWorkItemDocuments("w1");
    expect(body.work_item_id).toBe("w1");
    expect(f).toHaveBeenCalledWith("/api/work-items/w1/documents", expect.anything());
  });

  it("names the server when the request never leaves the browser", async () => {
    // what a stopped server actually produces: a bare `TypeError`, not a response
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api.listWorkItems()).rejects.toThrow(
      /could not reach the Kraft server \(GET \/work-items\?include_abandoned=true\)/,
    );
  });
});
