import { ABORT, HANG, type Answer, type MockRequest, type MockServer, type Reply } from "../e2e/contract/mocks/mockApi";

/**
 * What puts the mocked API (e2e/contract/mocks/mockApi.ts) under the page, so the
 * dashboard runs with no Kraft server: `fetch` and the events socket answered in the
 * page, the clock moved to the scenario's moment, and the two `<a href="/api/...">`
 * log links (a download, a new tab) served from the mock too.
 */

/** A request the shim answers: same origin, under /api/, as every call in src/api.ts is. */
const isApi = (u: URL) => u.origin === location.origin && u.pathname.startsWith("/api/");

const toResponse = (a: Answer) =>
  new Response(a.status === 204 || a.status === 304 ? null : (a.body ?? null), {
    status: a.status,
    headers: { ...(a.contentType ? { "content-type": a.contentType } : {}), ...a.headers },
  });

/**
 * `serve`, telling `see` each request it answered and how, once it has: what the
 * things-to-try list ticks from (tries.ts). A listener that throws loses the tick, not the answer.
 */
export function watch(serve: MockServer, see: (req: MockRequest, reply: Reply) => void): MockServer {
  return async (req) => {
    const reply = await serve(req);
    try { see(req, reply); } catch { /* the page still gets its answer */ }
    return reply;
  };
}

/** `fetch` for /api/** answered by `serve`; anything else goes to the network. */
export function patchFetch(serve: MockServer, win: Window & typeof globalThis = window) {
  const real = win.fetch.bind(win);
  win.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    // Decided before any Request is built: building one from a Request takes its body, and the network needs it.
    if (!isApi(new URL(input instanceof Request ? input.url : String(input), location.href))) return real(input, init);
    const request = new Request(input instanceof Request ? input : new URL(String(input), location.href), init);
    const text = request.method === "GET" || request.method === "HEAD" ? "" : await request.text();
    const reply = await serve({
      method: request.method,
      url: request.url,
      json: () => {
        try { return text ? JSON.parse(text) : null; } catch { return null; }
      },
    });
    // What a refused connection rejects with, so api.ts says the server is unreachable as it would.
    if (reply === ABORT) throw new TypeError("Failed to fetch");
    if (reply === HANG) {
      return new Promise<Response>((_, reject) => {
        if (request.signal.aborted) reject(request.signal.reason);
        else request.signal.addEventListener("abort", () => reject(request.signal.reason));
      });
    }
    return toResponse(reply);
  };
}

/** The open DemoSockets: where `broadcast` sends. One joins when it opens and leaves when it starts to close. */
const sockets = new Set<DemoSocket>();

/** The events socket with no server behind it: it opens, closes when asked, and carries the events the mock's
 *  actions append (`broadcast`), so the shell reads "live" and the board moves when an item does. */
export class DemoSocket extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readyState = DemoSocket.CONNECTING;
  readonly protocol = "";
  readonly extensions = "";
  readonly bufferedAmount = 0;
  binaryType: BinaryType = "blob";
  onopen: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: ((e: CloseEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;

  constructor(readonly url: string) {
    super();
    setTimeout(() => {
      if (this.readyState !== DemoSocket.CONNECTING) return;
      this.readyState = DemoSocket.OPEN;
      sockets.add(this);
      this.fire(new Event("open"));
    }, 0);
  }

  send() {}

  close() {
    if (this.readyState >= DemoSocket.CLOSING) return;
    this.readyState = DemoSocket.CLOSING;
    sockets.delete(this);
    setTimeout(() => {
      this.readyState = DemoSocket.CLOSED;
      this.fire(new CloseEvent("close", { code: 1000, wasClean: true }));
    }, 0);
  }

  /** One frame from the server, as a `message`. A listener that throws is reported, as a browser reports it, and
   *  stops nothing else: not the other sockets, and never the action that sent the frame. */
  receive(data: string) {
    try {
      this.fire(new MessageEvent("message", { data }));
    } catch (e) {
      console.error(e);
    }
  }

  private fire(e: Event) {
    this.dispatchEvent(e);
    (this[`on${e.type}` as "onopen"] as ((e: Event) => void) | null)?.(e);
  }
}

/** An event row to every open DemoSocket, as the server's socket sends it; one connecting or closing gets nothing.
 *  Sent on a later task, as a frame arrives: the action's own answer lands first, and nothing a listener does can
 *  reach back into the mock halfway through an action. */
export function broadcast(event: unknown) {
  // One task for everything sent until it runs: an action's frames arrive together, each its own message, so
  // nothing the first one sets off (a re-read) runs between them and stores the next one twice.
  if (!queued.length) setTimeout(flush, 0);
  queued.push(JSON.stringify(event));
}

/** The frames `broadcast` has sent and the next task delivers, in order. */
const queued: string[] = [];
function flush() {
  for (const data of queued.splice(0)) for (const s of [...sockets]) s.receive(data);
}

/** `new WebSocket(...)` for /api/** makes a DemoSocket; any other (Vite's dev reload socket) is the real one. */
export function patchWebSocket(win: Window & typeof globalThis = window) {
  win.WebSocket = new Proxy(win.WebSocket, {
    construct(target, args, newTarget) {
      const url = new URL(String(args[0]), location.href);
      if (url.host === location.host && url.pathname.startsWith("/api/")) return new DemoSocket(url.href);
      return Reflect.construct(target, args, newTarget);
    },
  });
}

/**
 * `Date` running from `to` instead of today, so the scenario's timestamps read as the
 * contract suite reads them ("17d 10h", "2 min ago"). The clock still advances, so a
 * running item's elapsed time ticks on.
 */
export function shiftClock(to: number, win: typeof globalThis = globalThis) {
  const Real = win.Date;
  const offset = to - Real.now();
  class DemoDate extends Real {
    constructor(...args: unknown[]) {
      if (args.length) super(...(args as [number]));
      else super(Real.now() + offset);
    }
    static now() {
      return Real.now() + offset;
    }
  }
  win.Date = DemoDate as DateConstructor;
}

/**
 * A plain link into /api/ (the log's Download, a red test's log) is a navigation, which
 * no fetch shim sees: answer it from the mock and hand the browser the result.
 */
export function serveApiLinks(doc: Document = document) {
  doc.addEventListener("click", async (e) => {
    const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
    if (!a || e.defaultPrevented || !isApi(new URL(a.href))) return;
    e.preventDefault();
    const download = a.hasAttribute("download");
    // A new tab is opened in the click itself, while it still counts as the person's: after the
    // awaits below a browser takes window.open for a popup and blocks it.
    const newTab = !download && ((a.target && a.target !== "_self") || e.metaKey || e.ctrlKey || e.shiftKey);
    const tab = newTab ? window.open("", "_blank") : null;
    if (tab) tab.opener = null;
    const res = await fetch(a.href);
    const href = URL.createObjectURL(await res.blob());
    if (download) {
      const save = doc.createElement("a");
      save.href = href;
      save.download = a.download;
      save.click();
    } else if (tab) tab.location.href = href;
    else if (!newTab) location.assign(href);
    setTimeout(() => URL.revokeObjectURL(href), 60_000);
  });
}

/**
 * GitHub Pages answers any address it has no file for with the site's 404 page, which
 * sends an address under the demo back to the demo's own page as `?to=` (see
 * docsite/nuxt.config.ts). Put it back before the router reads it.
 */
export function restoreAddress(base: string) {
  const to = new URLSearchParams(location.search).get("to");
  if (to?.startsWith("/") && !to.startsWith("//")) history.replaceState(null, "", base.replace(/\/$/, "") + to);
}
