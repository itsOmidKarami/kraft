import type { Api, ReviewThread } from "./api";

/** The events after which an item's threads may have changed (the web UI's `THREAD_EVENTS`). */
export const THREAD_EVENTS = /^(thread_updated|review_submitted|rewind_requested|rewind_cancelled|reply_agent_.+)$/;
export const COALESCE_MS = 250;

interface Entry {
  threads?: ReviewThread[];
  text?: string;
  error?: unknown;
  inflight?: Promise<void>;
  again?: boolean;
  timer?: ReturnType<typeof setTimeout>;
}

/**
 * Each item's review threads, as `GET /work-items/{id}/threads` last answered.
 * One request per item at a time: a load asked for while one runs makes
 * exactly one more after it, and every caller gets that newer answer.
 */
export class ThreadCache {
  private entries = new Map<string, Entry>();
  private changeL = new Set<(id: string) => void>();

  constructor(private api: Pick<Api, "listThreads">) {}

  get(id: string): ReviewThread[] | undefined {
    return this.entries.get(id)?.threads;
  }

  loaded(): string[] {
    return [...this.entries].filter(([, e]) => e.threads !== undefined).map(([id]) => id);
  }

  async load(id: string): Promise<ReviewThread[]> {
    let e = this.entries.get(id);
    if (!e) this.entries.set(id, (e = {}));
    if (e.inflight) e.again = true;
    else this.run(id, e);
    while (e.inflight) await e.inflight;
    if (e.error !== undefined) throw e.error;
    return e.threads!;
  }

  /** A load soon, coalesced; only for an item already loaded. */
  poke(id: string): void {
    const e = this.entries.get(id);
    if (!e || e.timer || (e.threads === undefined && !e.inflight)) return;
    e.timer = setTimeout(() => {
      e.timer = undefined;
      void this.load(id).catch(() => {});
    }, COALESCE_MS);
  }

  onEvent(ev: { type: string; work_item_id: string }): void {
    if (THREAD_EVENTS.test(ev.type)) this.poke(ev.work_item_id);
  }

  /** Fires only when an item's answer differs from the one before. */
  onChange(cb: (id: string) => void): () => void {
    this.changeL.add(cb);
    return () => this.changeL.delete(cb);
  }

  forget(id: string): void {
    const e = this.entries.get(id);
    if (e?.timer) clearTimeout(e.timer);
    this.entries.delete(id);
  }

  private run(id: string, e: Entry): void {
    e.again = false;
    const p = (async () => {
      try {
        const threads = await this.api.listThreads(id);
        e.error = undefined;
        const text = JSON.stringify(threads);
        if (text === e.text) return;
        e.threads = threads;
        e.text = text;
        if (this.entries.get(id) === e) this.changeL.forEach((cb) => cb(id));
      } catch (err) {
        e.error = err;
      }
    })();
    e.inflight = p;
    // Registered before any caller's await on `p`, so a waiting `load` sees the next run.
    void p.then(() => {
      if (e.again) this.run(id, e);
      else e.inflight = undefined;
    });
  }
}
