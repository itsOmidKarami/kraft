/** The window between confirming an action and sending it: Undo in it sends nothing. */
export const UNDO_WINDOW_MS = 5000;

const pending = new Set<(keepalive: boolean) => void>();
let listening = false;

/** Leaving the page inside the window sends what is waiting, at once, with `keepalive`
 *  so the browser finishes the request after the page is gone. */
function onPageHide() {
  for (const fire of [...pending]) fire(true);
}

/** Holds `send` for `ms`, then calls it once with `keepalive: false`. `undo()` before then
 *  drops it and returns true; after the send it returns false. On `pagehide` every pending
 *  action is sent at once with `keepalive: true`. Module state, not a component's: moving to
 *  another page inside the window still sends it. */
export function sendLater(send: (opts: { keepalive: boolean }) => Promise<unknown>, ms: number): { undo(): boolean } {
  if (!listening) {
    window.addEventListener("pagehide", onPageHide);
    listening = true;
  }
  let done = false;
  const settle = () => {
    done = true;
    clearTimeout(timer);
    pending.delete(fire);
  };
  const fire = (keepalive: boolean) => {
    if (done) return;
    settle();
    void send({ keepalive });
  };
  const timer = setTimeout(() => fire(false), ms);
  pending.add(fire);
  return {
    undo() {
      if (done) return false;
      settle();
      return true;
    },
  };
}

/** Drops every pending action and the listener, so no `pagehide` send leaks between tests. */
export function _resetLaterForTests() {
  pending.clear();
  window.removeEventListener("pagehide", onPageHide);
  listening = false;
}
