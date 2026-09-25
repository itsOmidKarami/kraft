import WebSocket from "ws";
import type { KraftEvent } from "../../../frontend/src/types";
import type { Api, WorkItem, WorkItemDetail } from "./api";

export interface Socket {
  onMessage(cb: (data: string) => void): void;
  onClose(cb: () => void): void;
  close(): void;
}
export type SocketFactory = (url: string, headers: Record<string, string>) => Socket;

export const wsSocket: SocketFactory = (url, headers) => {
  const ws = new WebSocket(url, { headers });
  return {
    onMessage: (cb) => ws.on("message", (d) => cb(d.toString())),
    onClose: (cb) => {
      ws.on("close", cb);
      ws.on("error", () => ws.close());
    },
    close: () => ws.close(),
  };
};

const MAX_DELAY = 30_000;

type Listener<T> = (v: T) => void;

export class Store {
  private byId = new Map<string, WorkItem>();
  private details = new Map<string, WorkItemDetail>();
  private socket?: Socket;
  private delay = 1_000;
  private timer?: ReturnType<typeof setTimeout>;
  private stopped = false;
  private lastDelays: number[] = [];
  private changeL = new Set<Listener<string[] | "all">>();
  private eventL = new Set<Listener<KraftEvent>>();
  private connL = new Set<Listener<boolean>>();
  connected = false;

  constructor(private api: Api, private factory: SocketFactory) {}

  items = () => [...this.byId.values()];
  item = (id: string) => this.byId.get(id);
  detail = (id: string) => this.details.get(id);

  onChange = (cb: Listener<string[] | "all">) => (this.changeL.add(cb), () => this.changeL.delete(cb));
  onEvent = (cb: Listener<KraftEvent>) => (this.eventL.add(cb), () => this.eventL.delete(cb));
  onConnection = (cb: Listener<boolean>) => (this.connL.add(cb), () => this.connL.delete(cb));

  async start(): Promise<void> {
    this.stopped = false;
    await this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.socket?.close();
  }

  async refresh(id: string): Promise<void> {
    try {
      const d = await this.api.getItem(id);
      this.byId.set(id, d);
      this.details.set(id, d);
    } catch (e) {
      if ((e as { status?: number }).status !== 404) throw e;
      this.byId.delete(id);
      this.details.delete(id);
    }
    this.changeL.forEach((cb) => cb([id]));
  }

  private async connect(): Promise<void> {
    try {
      const { items, cursor } = await this.api.listItems();
      this.byId = new Map(items.map((i) => [i.id, i]));
      this.details.clear();
      this.changeL.forEach((cb) => cb("all"));
      this.delay = 1_000;
      this.subscribe(cursor);
    } catch {
      this.setConnected(false);
      this.retry();
    }
  }

  private subscribe(cursor: number): void {
    const socket = this.factory(this.api.wsUrl(cursor), this.api.headers());
    this.socket = socket;
    socket.onMessage((data) => {
      const ev = JSON.parse(data) as KraftEvent;
      this.eventL.forEach((cb) => cb(ev));
      void this.refresh(ev.work_item_id).catch(() => {});
    });
    socket.onClose(() => {
      if (this.socket !== socket) return;
      this.setConnected(false);
      this.retry();
    });
    this.setConnected(true);
  }

  private retry(): void {
    if (this.stopped) return;
    const delay = this.delay;
    this.lastDelays.push(delay);
    this.delay = Math.min(this.delay * 2, MAX_DELAY);
    this.timer = setTimeout(() => void this.connect(), delay);
  }

  private setConnected(v: boolean): void {
    if (this.connected === v) return;
    this.connected = v;
    this.connL.forEach((cb) => cb(v));
  }
}
