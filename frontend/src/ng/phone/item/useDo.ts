import { useCallback, useState } from "react";
import { showToast } from "../../ui/Toast";
import type { Done } from "../../item/actions";

/** One write at a time: `run` waits for the server, says why on a refusal, and
 *  reloads the item on success (W17 brief 0.8). */
export function useDo(reload: () => void) {
  const [busy, setBusy] = useState(false);
  const run = useCallback(async <T,>(p: Promise<Done<T>>, ok?: string | ((b: T) => string)): Promise<Done<T>> => {
    setBusy(true);
    const r = await p;
    setBusy(false);
    if (r.ok) {
      if (ok) showToast(typeof ok === "function" ? ok(r.body) : ok);
      reload();
    } else showToast(r.error);
    return r;
  }, [reload]);
  return { busy, run };
}
