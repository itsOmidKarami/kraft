import WebSocket from "ws";
import type { KraftEvent } from "../../../frontend/src/types";
import type { Api, WorkItem, WorkItemDetail } from "./api";
import { shouldReread } from "./rereads";

/** Why the store is not connected: an API status (0 unreachable, -1 not an API error) and its detail. */
export interface ConnError { status: number; detail: string }

export interface Socket {
  onOpen(cb: () => void): void;
  onMessage(cb: (data: string) => void): void;
  // `err` is why it never opened, when it didn't.
  onClose(cb: (err?: ConnError) => void): void;
  close(): void;
}
export type SocketFactory = (url: string, headers: Record<string, string>) => Socket;

/** `ws` 8 reports a refused handshake only as an error naming the response code; no code means unreachable. */
export function handshakeError(e: Error): ConnError {
  const m = /Unexpected server response: (\d+)/.exec(e.message);
  return { status: m ? Number(m[1]) : 0, detail: e.message };
}

export const wsSocket: SocketFactory = (url, headers) => {
  // A daemon that accepts the connection and never answers fails like any other, and backs off.
  const ws = new WebSocket(url, { headers, handshakeTimeout: 10_000 });
  let err: Error | undefined;
  ws.on("error", (e) => {
    err = e;
    ws.close();
  });
  return {
    onOpen: (cb) => ws.on("open", cb),
    onMessage: (cb) => ws.on("message", (d) => cb(d.toString())),
    onClose: (cb) => ws.on("close", () => cb(err ? handshakeError(err) : undefined)),
    close: () => ws.close(),
  };
};

export type StoreLog = (level: "info" | "warn" | "error", msg: string) => void;

const MAX_DELAY = 30_000;

type Listener<T> = (v: T) => void;

function connError(e: unknown): ConnError {
  const { status, detail } = e as { status?: unknown; detail?: unknown };
  if (typeof status === "number") return { status, detail: typeof detail === "string" ? detail : String((e as Error).message) };
  return { status: -1, detail: String(e) };
}

export class Store {
  private byId = new Map<string, WorkItem>();
  private details = new Map<string, WorkItemDetail>();
  private socket?: Socket;
  private delay = 1_000;
  private timer?: ReturnType<typeof setTimeout>;
  private stopped = false;
  // Bumped by every connect: a list call that a newer connect, or stop(), has overtaken does nothing.
  private gen = 0;
  private lastDelays: number[] = [];
  // One re-read in flight per item; `again` is the one queued behind it. Kept across a
  // reconnect, so one already running is not joined by a second.
  private slots = new Map<string, { again: boolean }>();
  private changeL = new Set<Listener<string[] | "all">>();
  private eventL = new Set<Listener<KraftEvent>>();
  private connL = new Set<Listener<boolean>>();
  private errL = new Set<Listener<ConnError | null>>();
  connected = false;
  lastError: ConnError | null = null;

  constructor(private api: Api, private factory: SocketFactory, private log: StoreLog = () => {}) {}

  items = () => [...this.byId.values()];
  item = (id: string) => this.byId.get(id);
  detail = (id: string) => this.details.get(id);

  onChange = (cb: Listener<string[] | "all">) => (this.changeL.add(cb), () => this.changeL.delete(cb));
  onEvent = (cb: Listener<KraftEvent>) => (this.eventL.add(cb), () => this.eventL.delete(cb));
  onConnection = (cb: Listener<boolean>) => (this.connL.add(cb), () => this.connL.delete(cb));
  onError = (cb: Listener<ConnError | null>) => (this.errL.add(cb), () => this.errL.delete(cb));

  async start(): Promise<void> {
    this.stopped = false;
    await this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.clearTimer();
    this.dropSocket();
  }

  /** Reconnect now, from a 1s delay: whatever connect or retry was pending is dropped. */
  retryNow(): void {
    if (this.stopped) return;
    this.clearTimer();
    this.dropSocket();
    this.setConnected(false);
    this.delay = 1_000;
    this.log("info", "retrying now");
    void this.connect();
  }

  /** Re-read one item; false when the server no longer has it. */
  async refresh(id: string): Promise<boolean> {
    let found = true;
    try {
      const d = await this.api.getItem(id);
      this.byId.set(id, d);
      this.details.set(id, d);
    } catch (e) {
      if ((e as { status?: number }).status !== 404) throw e;
      this.byId.delete(id);
      this.details.delete(id);
      found = false;
    }
    this.changeL.forEach((cb) => cb([id]));
    return found;
  }

  private async connect(): Promise<void> {
    const gen = ++this.gen;
    try {
      const { items, cursor } = await this.api.listItems();
      if (gen !== this.gen || this.stopped) return;
      this.byId = new Map(items.map((i) => [i.id, i]));
      this.details.clear();
      this.changeL.forEach((cb) => cb("all"));
      this.subscribe(cursor);
    } catch (e) {
      if (gen !== this.gen || this.stopped) return;
      const err = connError(e);
      if (this.setError(err)) this.log("warn", `list failed: ${err.status} ${err.detail}`);
      this.setConnected(false);
      this.retry();
    }
  }

  private subscribe(cursor: number): void {
    const url = this.api.wsUrl(cursor);
    const socket = this.factory(url, this.api.headers());
    this.socket = socket;
    let opened = false;
    socket.onOpen(() => {
      if (this.socket !== socket || this.stopped) return;
      opened = true;
      this.delay = 1_000;
      this.setError(null);
      this.log("info", `connected to ${url.split("?")[0]}`);
      this.setConnected(true);
    });
    socket.onMessage((data) => {
      if (this.socket === socket) this.handle(data);
    });
    socket.onClose((err) => {
      if (this.socket !== socket) return;
      this.socket = undefined;
      if (!opened) this.setError(err ?? { status: 0, detail: "socket closed before open" });
      this.log("warn", opened ? "disconnected" : `disconnected: ${this.lastError?.detail}`);
      this.setConnected(false);
      this.retry();
    });
  }

  private handle(data: string): void {
    let ev: KraftEvent;
    try {
      ev = JSON.parse(data) as KraftEvent;
    } catch {
      this.log("warn", `ignored a malformed event: ${data.slice(0, 200)}`);
      return;
    }
    this.eventL.forEach((cb) => cb(ev));
    const id = ev.work_item_id;
    if (typeof id === "string" && shouldReread(ev.type, this.byId.has(id))) void this.queueRefresh(id);
  }

  /** At most one re-read in flight per item and one queued behind it; a burst shares the queued one. */
  private async queueRefresh(id: string): Promise<void> {
    const busy = this.slots.get(id);
    if (busy) {
      busy.again = true;
      return;
    }
    const slot = { again: false };
    this.slots.set(id, slot);
    try {
      do {
        slot.again = false;
        // Gone is gone: a queued re-read must not bring a dropped item back.
        if (!(await this.refreshLogged(id))) break;
      } while (slot.again);
    } finally {
      this.slots.delete(id);
    }
  }

  private async refreshLogged(id: string): Promise<boolean> {
    try {
      const found = await this.refresh(id);
      if (!found) this.log("info", `re-read ${id}: no longer on the server`);
      return found;
    } catch (e) {
      this.log("warn", `re-read ${id} failed: ${connError(e).detail}`);
      return true;
    }
  }

  private retry(): void {
    if (this.stopped) return;
    this.clearTimer();
    const delay = this.delay;
    this.lastDelays.push(delay);
    this.delay = Math.min(this.delay * 2, MAX_DELAY);
    this.log("info", `retrying in ${delay / 1000}s`);
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void this.connect();
    }, delay);
  }

  private clearTimer(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = undefined;
  }

  // Detached first, so its close is not taken for a disconnect.
  private dropSocket(): void {
    const s = this.socket;
    this.socket = undefined;
    s?.close();
  }

  private setError(e: ConnError | null): boolean {
    const was = this.lastError;
    if (was === e || (was && e && was.status === e.status && was.detail === e.detail)) return false;
    this.lastError = e;
    this.errL.forEach((cb) => cb(e));
    return true;
  }

  private setConnected(v: boolean): void {
    if (this.connected === v) return;
    this.connected = v;
    this.connL.forEach((cb) => cb(v));
  }
}
