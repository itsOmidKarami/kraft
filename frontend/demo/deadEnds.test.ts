// @vitest-environment node
import { describe, expect, it } from "vitest";
import { buildScenario } from "../e2e/contract/mocks/fixtures";
import { mockServer, type Answer } from "../e2e/contract/mocks/mockApi";
import { NG_NOW } from "../e2e/contract/mocks/ngItems";
import { demoOptions } from "./scenario";

/**
 * A control the demo draws leads somewhere: what a visitor opens from one answers, with the demo's own options
 * (`scenario.ts`). One row for each dead end that was found: an action whose answer names an item nobody made,
 * a link into a page the demo left empty.
 */
const demo = () => {
  const S = buildScenario("default");
  const serve = mockServer(S, { ...demoOptions, now: () => NG_NOW });
  const call = async (method: string, path: string, body?: unknown) => {
    const a = (await serve({ method, url: `http://x/api${path}`, json: () => body ?? null })) as Answer;
    return { status: a.status, body: a.body ? JSON.parse(a.body) : null };
  };
  return { S, call };
};
const beadId = (S: ReturnType<typeof demo>["S"], bead: string) => [...S.ngBoard, ...S.ngArchived].find((id) => S.bundles[id].item.bead_id === bead)!;

describe("Duplicate", () => {
  it.each([
    ["a cancelled item (its card says duplicating starts a new one)", "kraft-88ca"],
    ["an archived one", "kraft-9a02"],
    ["a finished one", "kraft-1b6e"],
  ])("%s opens a new paused item that has its title, brief, repo and chain", async (_name, bead) => {
    const { S, call } = demo();
    const id = beadId(S, bead);
    const was = (await call("GET", `/work-items/${id}`)).body;
    const made = await call("POST", `/work-items/${id}/duplicate`);
    expect(made.status).toBe(201);
    expect(made.body).toEqual({ id: expect.any(String), status: "paused" });
    expect(made.body.id).not.toBe(id);
    const copy = await call("GET", `/work-items/${made.body.id}`);
    expect(copy.status).toBe(200);
    expect(copy.body).toMatchObject({ title: was.title, description: was.description, repo: was.repo, chain_template: was.chain_template, status: "paused", current_node_id: null });
    expect((await call("GET", "/work-items")).body.items.map((i: any) => i.id)).toContain(made.body.id);
    expect((await call("GET", `/work-items/${id}`)).body).toEqual(was);
  });

  it("a copy of an item filed with a spec and a plan carries both, for another repo than the default", async () => {
    const { call } = demo();
    const attachments = [{ kind: "spec", path: "docs/specs/cache.md" }, { kind: "plan", path: "docs/plans/cache.md" }];
    const filed = await call("POST", "/work-items", { title: "Cache embeddings", description: "Key them by content hash.", repo: "kraft-plugins", attachments });
    expect(filed.status).toBe(201);
    const was = (await call("GET", `/work-items/${filed.body.id}`)).body;
    expect(was.attachments).toEqual(attachments);
    const made = await call("POST", `/work-items/${filed.body.id}/duplicate`);
    expect(made.status).toBe(201);
    expect((await call("GET", `/work-items/${made.body.id}`)).body).toMatchObject({ title: "Cache embeddings", description: "Key them by content hash.", repo: "kraft-plugins", attachments, status: "paused", current_node_id: null });
    expect((await call("GET", `/work-items/${filed.body.id}`)).body).toEqual(was);
  });

  it("an item nobody made is the server's 404", async () => {
    expect((await demo().call("POST", "/work-items/nope/duplicate")).status).toBe(404);
  });
});

describe("the chain editor's links into the Library", () => {
  /** Every component a chain draft's nodes and tasks extend, by the library section it is looked up in. */
  const extended = (chain: { nodes?: any[] }) => {
    const found: [section: string, name: string][] = [];
    const walk = (x: any, section: string) => {
      if (Array.isArray(x)) return x.forEach((v) => walk(v, section));
      if (typeof x !== "object" || x === null) return;
      if (typeof x.extends === "string") found.push([section, x.extends]);
      for (const v of Object.values(x)) walk(v, "tasks");
    };
    for (const n of chain.nodes ?? []) walk(n, "nodes");
    return found;
  };

  it("every component the default chain extends is in the Library", async () => {
    const { call } = demo();
    const chain = (await call("GET", "/drafts/chains/default")).body.result.model["chains/default.yaml"];
    const library = (await call("GET", "/drafts/library/library")).body.result.model["library.yaml"];
    const used = extended(chain);
    expect(used.length).toBeGreaterThan(10);
    expect(used.filter(([section, name]) => !(name in (library[section] ?? {}))).map(([section, name]) => `${section}.${name}`)).toEqual([]);
  });
});
