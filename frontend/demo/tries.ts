import { ABORT, HANG, type Answer, type MockRequest, type MockServer, type Reply } from "../e2e/contract/mocks/mockApi";

/**
 * The demo's things to try: five entries, each ticked when the shim sees the request that does it
 * (`tickOf`, fed by shim.ts's `watch`), and each with a Show me that goes where it can be done now
 * (`TRIES[].where`, read straight from the mock so the reading itself ticks nothing).
 */

export type TryId = "open" | "gate" | "review" | "steer" | "shape";

/** Where a Show me goes: an app path (no base), and the control it rings there, by role and accessible name. */
export interface Spot { path: string; role: string; name: string | RegExp }

/** What the entries read to find their item: the board's rows, and one item's detail. */
export interface Look { list(): Promise<{ id: string }[]>; item(id: string): Promise<Item | null> }
type Item = { id: string; status?: string; display_status?: string; pending_gate?: string | null; current_node_id?: string | null };

export interface Try {
  id: TryId;
  title: string;
  detail: string;
  /** Said in place of Show me when there is nothing to show it on. */
  none: string;
  where: (look: Look) => Promise<Spot | null>;
}

/** The mock read directly, past `watch`: a Show me looking for its item is not the visitor opening one. */
export function lookIn(serve: MockServer, origin = location.origin): Look {
  const get = async (path: string) => {
    const r = await serve({ method: "GET", url: `${origin}/api${path}`, json: () => null });
    if (r === ABORT || r === HANG || r.status !== 200 || !r.body) return null;
    try { return JSON.parse(r.body); } catch { return null; }
  };
  return {
    list: async () => (await get("/work-items"))?.items ?? [],
    item: (id) => get(`/work-items/${encodeURIComponent(id)}`),
  };
}

/** The first board item, by its detail (the board's row can lag it), that passes the first test any item passes. */
async function first(look: Look, ...tests: ((i: Item) => boolean)[]): Promise<Item | null> {
  const items = (await Promise.all((await look.list()).map((r) => look.item(r.id)))).filter((i): i is Item => !!i);
  for (const test of tests) {
    const hit = items.find(test);
    if (hit) return hit;
  }
  return null;
}

const running = (i: Item) => i.display_status === "running";
const active = (i: Item) => i.status === "active";
const atGate = (i: Item) => !!i.pending_gate;
const begunPause = (i: Item) => i.status === "paused" && !!i.current_node_id;
const page = (i: Item) => `/work-items/${encodeURIComponent(i.id)}`;
const ON_ITS_WAY = "one is on its way";

export const TRIES: Try[] = [
  {
    id: "open", title: "Open a running item", detail: "Its chain, and the node it is on.", none: `Nothing is running right now; ${ON_ITS_WAY}.`,
    where: async (look) => {
      const i = await first(look, running, active);
      return i && { path: page(i), role: "button", name: /, node, running\b/ };
    },
  },
  {
    id: "gate", title: "Approve a gate", detail: "Read what it waits on, then approve it.", none: `No item waits at a gate right now; ${ON_ITS_WAY}.`,
    where: async (look) => {
      const i = await first(look, atGate);
      return i && { path: page(i), role: "button", name: "Review and decide" };
    },
  },
  {
    id: "review", title: "Review the changes", detail: "The diff, a comment, request changes.", none: `Nothing has changes to review right now; ${ON_ITS_WAY}.`,
    where: async (look) => {
      const i = await first(look, atGate, running, active);
      return i && { path: `${page(i)}/review`, role: "button", name: "Request changes" };
    },
  },
  {
    id: "steer", title: "Steer an agent", detail: "Pause it, then resume with a steer.", none: `Nothing is running right now; ${ON_ITS_WAY}.`,
    // A paused one first, where the steer box is already open: half the way there.
    where: async (look) => {
      const i = await first(look, begunPause, running);
      if (!i) return null;
      return running(i) ? { path: page(i), role: "button", name: "Pause" } : { path: page(i), role: "textbox", name: "Steer" };
    },
  },
  {
    id: "shape", title: "Shape the process", detail: "The Chains editor and the Library.", none: "",
    where: async () => ({ path: "/templates/chains", role: "button", name: "Add a node or gate here" }),
  },
];

const ok = (r: Reply): r is Answer => typeof r === "object" && r.status >= 200 && r.status < 300;
const said = (s: unknown) => typeof s === "string" && s.trim() !== "";
const parsed = (body?: string) => {
  try { return body ? JSON.parse(body) : null; } catch { return null; }
};

/**
 * What a request the mock answered ticks. `here` is the page the visitor is on, as the router names it:
 * an item's detail read only counts as opening it while its page (or its peek on the board) shows it,
 * so a read the app makes on its own (a re-read after an event) ticks nothing. A refused request ticks nothing.
 */
export function tickOf(req: MockRequest, reply: Reply, here: { pathname: string; search: string }): TryId[] {
  if (!ok(reply)) return [];
  const p = new URL(req.url).pathname.replace(/^.*?\/api(?=\/)/, "");
  let m: RegExpMatchArray | null;
  if (req.method === "GET") {
    if ((m = p.match(/^\/work-items\/([^/]+)$/))) {
      const id = decodeURIComponent(m[1]);
      const at = decodeURIComponent(here.pathname);
      const shown = at === `/work-items/${id}` || at.startsWith(`/work-items/${id}/nodes/`) || (at === "/" && new URLSearchParams(here.search).get("sel") === id);
      return shown && parsed(reply.body)?.status === "active" ? ["open"] : [];
    }
    return /^\/drafts\/(chains|library)\/[^/]+$/.test(p) ? ["shape"] : [];
  }
  if (req.method !== "POST") return [];
  const body = req.json() ?? {};
  if (/^\/work-items\/[^/]+\/gates\/[^/]+\/approve$/.test(p)) return ["gate"];
  if ((m = p.match(/^\/work-items\/[^/]+(\/gates\/[^/]+)?\/review$/))) return m[1] && body.outcome === "approve" ? ["gate", "review"] : ["review"];
  if (/^\/work-items\/[^/]+\/threads$/.test(p) || /^\/threads\/[^/]+\/comments$/.test(p)) return ["review"];
  if (/^\/work-items\/[^/]+\/resume$/.test(p) && (said(body.steer) || Object.values(body.steers ?? {}).some(said))) return ["steer"];
  return [];
}

/** The list's state for the page load: what is ticked, told to whoever draws it. A reload starts over, as the data does. */
export class Tries {
  readonly done = new Set<TryId>();
  private readonly listeners = new Set<(moved: boolean) => void>();

  constructor(private readonly here: () => { pathname: string; search: string }) {}

  /** shim.ts's `watch` listener. `moved`: a request that may have changed the data, so a Show me may go elsewhere now. */
  readonly see = (req: MockRequest, reply: Reply) => {
    let ticked = false;
    for (const id of tickOf(req, reply, this.here())) {
      if (this.done.has(id)) continue;
      this.done.add(id);
      ticked = true;
    }
    const moved = req.method !== "GET";
    if (ticked || moved) for (const f of this.listeners) f(moved);
  };

  onChange(f: (moved: boolean) => void): () => void {
    this.listeners.add(f);
    return () => this.listeners.delete(f);
  }
}
