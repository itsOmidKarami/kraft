import { onApplyFrame } from "./apply/store";

export interface LiveFrame {
  type: string;
  payload: unknown;
}

const listeners = new Map<string, Set<(payload: unknown) => void>>();

/** Hears each live frame of one type (`intake_checked`). The one event socket (session.ts) hands every live frame to `onLiveFrame`; a page subscribes to its own type and never opens a socket. */
export function subscribeLive(type: string, listener: (payload: unknown) => void): () => void {
  const set = listeners.get(type) ?? new Set();
  set.add(listener);
  listeners.set(type, set);
  return () => void set.delete(listener);
}

/** Dispatches a live frame by its type: `apply_changed` to the apply chip, the rest to whoever subscribed. */
export function onLiveFrame(frame: LiveFrame) {
  onApplyFrame(frame);
  for (const l of listeners.get(frame.type) ?? []) l(frame.payload);
}
