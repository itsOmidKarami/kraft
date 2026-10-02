import { create } from "zustand";
import { useStore } from "../../store";
import { jsonBody, request } from "../http";
import { showToast } from "../ui/Toast";

export interface ApplyItem {
  id: string;
  file: string;
  text: string;
  problem?: string;
}
interface Pending {
  restart: ApplyItem[];
  reload: ApplyItem[];
  managed: boolean;
}
export type Phase = "idle" | "reloading" | "restarting" | "stuck";

interface ApplyState extends Pending {
  loaded: boolean;
  phase: Phase;
  /** The restart confirmation is open (the chip and the dialog share it). */
  confirming: boolean;
  /** Where the restarted server answers, while `phase` is "restarting". */
  address: string;
  /** Items with a running agent when the confirmation opened; null when the count was not read. */
  active: number | null;
  error: string | null;
  refresh: () => Promise<void>;
  runReload: () => Promise<void>;
  askRestart: () => Promise<void>;
  cancelRestart: () => void;
  runRestart: () => Promise<void>;
}

export const POLL_MS = 1000;
export const GIVE_UP_MS = 60_000;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Answers at `base`? A cross-origin answer is opaque but proves the server is up. */
async function answers(base: string): Promise<boolean> {
  try {
    await fetch(`${base}/api/health`, { mode: base === location.origin ? "same-origin" : "no-cors", cache: "no-store" });
    return true;
  } catch {
    return false;
  }
}

/** The bind and port Kraft runs on after a restart. A saved value the
 *  server lists as a restart item (`access.bind`, `access.port`) takes
 *  effect; otherwise the running one stays, either because it is the saved
 *  one or because KRAFT_HOST / KRAFT_PORT wins over the file, and the server
 *  then raises no item for it. */
export function afterRestart(saved: { bind: string; port: number }, running: { bind?: string | null; port?: number | null } | null, items: Pick<ApplyItem, "id">[]): { bind: string; port: number } {
  const pending = (key: "bind" | "port") => items.some((i) => i.id === `access.${key}`);
  return {
    bind: pending("bind") ? saved.bind : (running?.bind ?? saved.bind),
    port: pending("port") ? saved.port : (running?.port ?? saved.port),
  };
}

/** The address a restart brings Kraft back at: the port it will run on (`afterRestart`), on the host this page was opened on. */
async function restartAddress(items: ApplyItem[]): Promise<string> {
  const [a, h] = await Promise.all([request<{ bind: string; port?: number }>("/access"), request<{ bind?: string | null; port?: number | null }>("/health")]);
  const saved = a.status === 200 && a.body.port ? a.body.port : Number(location.port) || undefined;
  const port = saved && afterRestart({ bind: a.body.bind, port: saved }, h.status === 200 ? h.body : null, items).port;
  return `${location.protocol}//${location.hostname}${port ? `:${port}` : ""}`;
}

/** How many items are `active`, the count `kraft admin restart` shows before it asks. */
async function activeItems(): Promise<number | null> {
  const r = await request<{ items?: { status?: string }[] }>("/work-items");
  return r.status === 200 && Array.isArray(r.body.items) ? r.body.items.filter((i) => i.status === "active").length : null;
}

/** What a restart does to running work, for the confirmations: a restart ends each active item's
 *  agent, and the item stops as failed until someone retries it (`install#a-restart-ends-running-agents`). */
export function restartNote(active: number | null | undefined): string {
  if (active === 0) return "No item is active, so no agent is stopped.";
  const avoid = " Pause them first to avoid that.";
  if (active == null) return `Restarting stops the agent of any active item. Each stops as failed and needs Retry afterwards.${avoid}`;
  if (active === 1) return `1 item is active. Restarting stops its agent; it stops as failed and needs Retry afterwards. Pause it first to avoid that.`;
  return `${active} items are active. Restarting stops their agents; each stops as failed and needs Retry afterwards.${avoid}`;
}

/** A bind address as the host of a URL to it: a wildcard bind is reached on
 *  the host this page was opened on, and an IPv6 address goes in brackets
 *  (`http://::1:8765` is not a URL). */
export const bindHost = (bind: string): string =>
  bind === "0.0.0.0" ? location.hostname : bind.includes(":") && !bind.startsWith("[") ? `[${bind}]` : bind;

/** What to do when no service manager started Kraft: `kraft admin restart`
 *  brings a detached server back, but only stops one attached to a terminal
 *  (`cli/admin.py` `_cmd_restart`), which that terminal starts again. */
export const SELF_RESTART = "Kraft was not started as a service, so it cannot restart itself. Run kraft admin restart; if it runs attached to a terminal, that only stops it: stop it there with Ctrl-C and start it again the same way.";

export const useApply = create<ApplyState>((set, get) => ({
  restart: [],
  reload: [],
  managed: false,
  loaded: false,
  phase: "idle",
  confirming: false,
  address: "",
  active: null,
  error: null,

  refresh: async () => {
    const r = await request<Pending>("/apply");
    if (r.status === 200) set({ restart: r.body.restart, reload: r.body.reload, managed: r.body.managed === true, loaded: true });
  },

  runReload: async () => {
    set({ phase: "reloading", error: null });
    const r = await request<Pending>("/apply/reload", jsonBody("POST"));
    if (r.status !== 200) {
      set({ phase: "idle", error: "Could not reload." });
      return;
    }
    const bad = r.body.reload.filter((i) => i.problem).length;
    set({ restart: r.body.restart, reload: r.body.reload, managed: r.body.managed === true, phase: "idle" });
    showToast(bad ? `Reloaded, with ${bad} problem${bad > 1 ? "s" : ""}` : "Reloaded · nothing was interrupted");
  },

  askRestart: async () => {
    if (!get().managed) return;
    set({ active: null });
    const [address, active] = await Promise.all([restartAddress(get().restart), activeItems()]);
    set({ address, active, confirming: true });
  },
  cancelRestart: () => set({ confirming: false }),

  // The one call to /apply/restart in the UI: reached only from the confirm dialog.
  runRestart: async () => {
    set({ confirming: false, error: null });
    if (!get().managed) return;
    const { address } = get();
    const r = await request("/apply/restart", jsonBody("POST"));
    if (r.status === 409) {
      set({ managed: false, error: SELF_RESTART });
      return;
    }
    if (r.status !== 202) {
      set({ error: "Could not start the restart." });
      return;
    }
    set({ phase: "restarting" });
    const sameOrigin = address === location.origin;
    const start = Date.now();
    // Same address: the old process still answers for a moment, so down-then-up is the proof.
    let down = !sameOrigin;
    while (Date.now() - start < GIVE_UP_MS) {
      await sleep(POLL_MS);
      const up = await answers(address);
      if (!up) down = true;
      else if (down) {
        if (sameOrigin) {
          set({ phase: "idle" });
          await get().refresh();
          showToast("Restarted · changes applied");
        } else {
          location.assign(address);
        }
        return;
      }
    }
    set({ phase: "stuck", error: "Kraft did not come back. Run kraft admin health, or read server.log in the run directory." });
  },
}));

/** Keeps the store current: `apply_changed` live frames (see `onApplyFrame`),
 *  every reconnect of the event socket (the frame is never replayed), and the
 *  window gaining focus. */
export function watchApply(): () => void {
  const refresh = () => void useApply.getState().refresh();
  refresh();
  window.addEventListener("focus", refresh);
  let was = useStore.getState().connection === "open";
  const stop = useStore.subscribe((s) => {
    const open = s.connection === "open";
    if (open && !was) refresh();
    was = open;
  });
  return () => {
    window.removeEventListener("focus", refresh);
    stop();
  };
}

export const onApplyFrame = (frame: { type: string }) => {
  if (frame.type === "apply_changed") void useApply.getState().refresh();
};
