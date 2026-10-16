import { describe, expect, it, vi } from "vitest";
import { buildScenario } from "../e2e/contract/mocks/fixtures";
import { ABORT, mockServer, type Reply } from "../e2e/contract/mocks/mockApi";
import { lookIn, tickOf, TRIES, Tries, type TryId } from "./tries";

const ORIGIN = "http://demo.test";
const req = (method: string, path: string, body: unknown = null) => ({ method, url: `${ORIGIN}/api${path}`, json: () => body });
const answer = (body: unknown = {}, status = 200): Reply => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const at = (pathname: string, search = "") => ({ pathname, search });
const BOARD = at("/");
/** An item's detail as the mock answers it while an agent runs on its current node. */
const RUNNING = {
  status: "active", current_node_id: "implement",
  chain_definition: { nodes: [{ id: "spec_approval", kind: "gate" }, { id: "implement", kind: "exec" }] },
  worker_sessions: [{ node_id: "implement", status: "running" }],
};

/** The demo's own sample data, answered by the mock it runs on. */
const sample = () => {
  const serve = mockServer(buildScenario("default"), { ngBoard: true });
  return { serve, look: lookIn(serve, ORIGIN) };
};
const where = (id: TryId, look: ReturnType<typeof lookIn>) => TRIES.find((t) => t.id === id)!.where(look);

describe("tickOf", () => {
  it.each<[string, ReturnType<typeof req>, Reply, ReturnType<typeof at>, TryId[]]>([
    ["a gate's approve", req("POST", "/work-items/w1/gates/spec_approval/approve"), answer(), BOARD, ["gate"]],
    ["a review that approves a gate", req("POST", "/work-items/w1/gates/final_review/review", { outcome: "approve" }), answer(), BOARD, ["gate", "review"]],
    ["a review that requests changes at a gate", req("POST", "/work-items/w1/gates/final_review/review", { outcome: "request_changes" }), answer(), BOARD, ["review"]],
    ["a review with no gate", req("POST", "/work-items/w1/review", { outcome: "approve" }), answer(), BOARD, ["review"]],
    ["a new comment on the diff", req("POST", "/work-items/w1/threads", { body: "why?" }), answer(), BOARD, ["review"]],
    ["a reply in a thread", req("POST", "/threads/th1/comments", { body: "fixed" }), answer(), BOARD, ["review"]],
    ["a resume with a steer", req("POST", "/work-items/w1/resume", { steer: "look at the cache" }), answer(), BOARD, ["steer"]],
    ["a resume steering one task", req("POST", "/work-items/w1/resume", { steer: null, steers: { "verification.review.code_review": "be strict" } }), answer(), BOARD, ["steer"]],
    ["a resume with no steer", req("POST", "/work-items/w1/resume", { steer: null }), answer(), BOARD, []],
    ["a resume with a blank steer", req("POST", "/work-items/w1/resume", { steer: "  " }), answer(), BOARD, []],
    ["a pause", req("POST", "/work-items/w1/pause"), answer(), BOARD, []],
    ["Request changes from the gate pane (a reject)", req("POST", "/work-items/w1/gates/final_review/reject", { note: "bound the cache" }), answer(), BOARD, ["review"]],
    ["a running item's page reading it", req("GET", "/work-items/w1"), answer(RUNNING), at("/work-items/w1"), ["open"]],
    ["a running item's node page reading it", req("GET", "/work-items/w1"), answer(RUNNING), at("/work-items/w1/nodes/verification"), ["open"]],
    ["its peek on the board reading it", req("GET", "/work-items/w1"), answer(RUNNING), at("/", "?sel=w1"), ["open"]],
    ["a read of it while another page shows", req("GET", "/work-items/w1"), answer(RUNNING), at("/work-items/w2"), []],
    ["a read of it from the board with no peek", req("GET", "/work-items/w1"), answer(RUNNING), BOARD, []],
    ["its review page reading it", req("GET", "/work-items/w1"), answer(RUNNING), at("/work-items/w1/review"), []],
    ["a paused item's page reading it", req("GET", "/work-items/w1"), answer({ ...RUNNING, status: "paused" }), at("/work-items/w1"), []],
    ["an item just past its gate, still standing on it", req("GET", "/work-items/w1"), answer({ ...RUNNING, current_node_id: "spec_approval", worker_sessions: [{ node_id: "spec_approval", status: "running" }] }), at("/work-items/w1"), []],
    ["an active item with no session running", req("GET", "/work-items/w1"), answer({ ...RUNNING, worker_sessions: [{ node_id: "implement", status: "done" }] }), at("/work-items/w1"), []],
    ["a resumed item whose session has not started yet", req("GET", "/work-items/w1"), answer({ ...RUNNING, worker_sessions: [{ node_id: "implement", status: "pending" }] }), at("/work-items/w1"), ["open"]],
    ["the Chains editor's read", req("GET", "/drafts/chains/default"), answer(), at("/templates/chains"), ["shape"]],
    ["the Library's read", req("GET", "/drafts/library/library"), answer(), at("/templates/library"), ["shape"]],
    ["the composer's list of chains", req("GET", "/templates/chains"), answer(), at("/work-items/new"), []],
    ["an approve the server refused", req("POST", "/work-items/w1/gates/spec_approval/approve"), answer({ detail: "gate is not pending" }, 409), BOARD, []],
    ["a comment that never reached the server", req("POST", "/work-items/w1/threads", { body: "x" }), ABORT, BOARD, []],
  ])("%s", (_, r, reply, here, ticks) => {
    expect(tickOf(r, reply, here)).toEqual(ticks);
  });
});

describe("Tries", () => {
  it("ticks an entry once, and tells its listeners what moved", () => {
    const tries = new Tries(() => BOARD);
    const heard = vi.fn();
    tries.onChange(heard);
    tries.see(req("POST", "/work-items/w1/gates/g/approve"), answer());
    tries.see(req("POST", "/work-items/w1/gates/g/approve"), answer());
    tries.see(req("GET", "/health"), answer());
    expect([[...tries.done], heard.mock.calls]).toEqual([["gate"], [[true], [true]]]);
  });
});

describe("Tries.moved", () => {
  it("ticks nothing for an event the mock sent on its own, but tells the list things moved", () => {
    const tries = new Tries(() => at("/work-items/w1"));
    const heard = vi.fn();
    tries.onChange(heard);
    tries.moved();
    expect([[...tries.done], heard.mock.calls]).toEqual([[], [[true]]]);
  });
});

describe("TRIES' Show me", () => {
  it("goes to the item waiting at a gate on the sample data, and rings its banner's Review", async () => {
    const { serve, look } = sample();
    const items: { id: string; pending_gate: string | null }[] = JSON.parse(((await serve(req("GET", "/work-items"))) as { body: string }).body).items;
    const waiting = items.find((i) => i.pending_gate)!;
    expect(await where("gate", look)).toEqual({ path: `/work-items/${waiting.id}`, role: "button", name: "Review" });
  });

  it("finds no gate once the only one is approved", async () => {
    const { serve, look } = sample();
    const spot = await where("gate", look);
    await serve(req("POST", `${spot!.path}/gates/final_review/approve`));
    expect(await where("gate", look)).toBeNull();
    expect(TRIES.find((t) => t.id === "gate")!.none).toMatch(/one is on its way/);
  });

  it("once the visitor pauses the running item, Steer goes to that item's steer box, not the sample's paused one", async () => {
    const { serve, look } = sample();
    const running = (await where("steer", look))!;
    await serve(req("POST", `${running.path}/pause`));
    expect([running.name, await where("steer", look)]).toEqual(["Pause", { path: running.path, role: "textbox", name: "Steer" }]);
  });

  it("once the gate is approved, Open and Review go to an item that is really running, not the one still on its gate", async () => {
    const { serve, look } = sample();
    const gate = (await where("gate", look))!.path;
    await serve(req("POST", `${gate}/gates/final_review/approve`));
    const [open, review] = [await where("open", look), await where("review", look)];
    expect([open?.path === gate, open?.name, review?.path === `${gate}/review`, review?.name]).toEqual([false, /, node, running\b/, false, "Comment on this file"]);
  });

  it.each<[TryId, RegExp, string, string | RegExp]>([
    ["open", /^\/work-items\/[0-9a-f]{32}$/, "button", /, node, running\b/],
    ["review", /^\/work-items\/[0-9a-f]{32}\/review$/, "button", "Request changes"],
    ["steer", /^\/work-items\/[0-9a-f]{32}$/, "button", "Pause"],
    ["shape", /^\/templates\/chains$/, "button", "Add a node or gate here"],
  ])("%s goes where it can be done on the sample data", async (id, path, role, name) => {
    const spot = await where(id, sample().look);
    expect(spot).toMatchObject({ role, name });
    expect(spot!.path).toMatch(path);
  });
});
