import { ABORT, HANG, type Answer, type MockRequest, type MockServer, type Reply } from "../e2e/contract/mocks/mockApi";
import { LIVE_SESSION_STATUSES, type SessionStatus } from "../src/types/vocab.generated";

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
type Item = {
  id: string; status?: string; display_status?: string; pending_gate?: string | null; current_node_id?: string | null;
  chain_definition?: { nodes?: { id: string; kind?: string }[] };
  worker_sessions?: { node_id?: string | null; status?: string }[];
};

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

/** How an entry picks its item: by the board's row, which is the item as it is now, less its sessions. */
type Test = (row: Item, look: Look) => boolean | Promise<boolean>;

/** The first board row that passes the first test any row passes. */
async function first(look: Look, ...tests: Test[]): Promise<Item | null> {
  const rows = (await look.list()) as Item[];
  for (const test of tests) for (const row of rows) if (await test(row, look)) return row;
  return null;
}

/**
 * Really running: active, on a node that is not a gate, with a live session there (running, or pending as
 * a resumed one is until it starts: the page draws that node running too). An item whose gate was just
 * approved is active but still stands on the gate, and its page draws no running node.
 */
export const running = (i: Item) =>
  i.status === "active" &&
  !!i.current_node_id &&
  i.chain_definition?.nodes?.find((n) => n.id === i.current_node_id)?.kind !== "gate" &&
  !!i.worker_sessions?.some((s) => s.node_id === i.current_node_id && LIVE_SESSION_STATUSES.includes(s.status as SessionStatus));
/** `running`, for a board row: the sessions it needs are on the detail, read only for a row that could pass. */
const runningRow: Test = async (row, look) => row.status === "active" && running((await look.item(row.id)) ?? row);
const atGate = (i: Item) => !!i.pending_gate;
const begunPause = (i: Item) => i.status === "paused" && !!i.current_node_id;
const page = (i: Item) => `/work-items/${encodeURIComponent(i.id)}`;
const ON_ITS_WAY = "one is on its way";

export const TRIES: Try[] = [
  {
    id: "open", title: "Open a running item", detail: "Its chain, and the node it is on.", none: `Nothing is running right now; ${ON_ITS_WAY}.`,
    where: async (look) => {
      const i = await first(look, runningRow);
      return i && { path: page(i), role: "button", name: /, node, running\b/ };
    },
  },
  {
    id: "gate", title: "Approve a gate", detail: "Read what it waits on, then approve it.", none: `No item waits at a gate right now; ${ON_ITS_WAY}.`,
    where: async (look) => {
      const i = await first(look, atGate);
      return i && { path: page(i), role: "button", name: "Review" };
    },
  },
  {
    id: "review", title: "Review the changes", detail: "The diff, a comment, request changes.", none: `Nothing has changes to review right now; ${ON_ITS_WAY}.`,
    // At a gate the review page's bar has Request changes; with none waiting, a running item's diff still
    // takes a comment, which ticks it too (the bar only draws Request changes at a gate).
    where: async (look) => {
      const i = await first(look, atGate, runningRow);
      if (!i) return null;
      return { path: `${page(i)}/review`, role: "button", name: atGate(i) ? "Request changes" : "Comment on this file" };
    },
  },
  {
    id: "steer", title: "Steer an agent", detail: "Pause it, then resume with a steer.", none: `Nothing is running right now; ${ON_ITS_WAY}.`,
    // A running one first, as the entry says: Pause, then the paused card's steer box. Else one already paused.
    where: async (look) => {
      const i = await first(look, runningRow, begunPause);
      if (!i) return null;
      return begunPause(i) ? { path: page(i), role: "textbox", name: "Steer" } : { path: page(i), role: "button", name: "Pause" };
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
      const item = parsed(reply.body);
      return shown && item && running(item) ? ["open"] : [];
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
