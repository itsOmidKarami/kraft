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

/** The address a restart brings Kraft back at: the saved port, on the host this page was opened on. */
async function restartAddress(): Promise<string> {
  const a = await request<{ port?: number }>("/access");
  const port = a.status === 200 && a.body.port ? a.body.port : Number(location.port) || undefined;
  return `${location.protocol}//${location.hostname}${port ? `:${port}` : ""}`;
}

export const useApply = create<ApplyState>((set, get) => ({
  restart: [],
  reload: [],
  managed: false,
  loaded: false,
  phase: "idle",
  confirming: false,
  address: "",
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
    set({ address: await restartAddress(), confirming: true });
  },
  cancelRestart: () => set({ confirming: false }),

  // The one call to /apply/restart in the UI: reached only from the confirm dialog.
  runRestart: async () => {
    set({ confirming: false, error: null });
    if (!get().managed) return;
    const { address } = get();
    const r = await request("/apply/restart", jsonBody("POST"));
    if (r.status === 409) {
      set({ managed: false, error: "Started from a terminal: restart it yourself (kraft admin restart)." });
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
