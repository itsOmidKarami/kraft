import { maybeNotify } from "./browserNotify";
import { useStore } from "./store";

const BACKOFF = [1000, 2000, 5000, 10000];

export function connectEvents(): () => void {
  let attempt = 0;
  let stopped = false;
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const open = () => {
    if (stopped) return;
    const seq = useStore.getState().lastSeq;
    socket = new WebSocket(
      `${location.origin.replace(/^http/, "ws")}/api/ws/events?after_seq=${seq}`,
    );
    socket.onopen = () => {
      attempt = 0;
      useStore.getState().setConnection("open");
    };
    socket.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      useStore.getState().applyEvent(ev);
      maybeNotify(ev, useStore.getState().workItems[ev.work_item_id]?.title ?? "Kraft");
    };
    const retry = () => {
      // A broken socket fires 'error' then 'close'; detach both so only the
      // first schedules a reconnect (otherwise attempt double-increments and
      // the first timer leaks a duplicate WebSocket).
      if (socket) socket.onclose = socket.onerror = null;
      if (stopped) return;
      useStore.getState().setConnection("reconnecting");
      const wait = BACKOFF[Math.min(attempt, BACKOFF.length - 1)];
      attempt += 1;
      timer = setTimeout(open, wait);
    };
    socket.onclose = retry;
    socket.onerror = retry;
  };

  open();

  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    socket?.close();
  };
}
