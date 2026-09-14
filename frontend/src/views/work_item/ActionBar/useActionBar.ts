import { useState } from "react";
import { showToast } from "../../../components/Toast";
import { useStore } from "../../../store";

/** The shared submit/busy/error/toast wrapper every composer and every
 *  plain-click button in the action bar uses.
 *
 *  W6.3: an action acts on the id it was invoked with (`itemId`), never on
 *  anything the response names. After it succeeds the bar is `pending` until
 *  the store has re-read that item, so a card never offers Approve again for
 *  a gate the server already cleared. `run` resolves true on success, so a
 *  composer can close itself. */
export function useActionBar(itemId: string) {
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const hydrateItem = useStore((s) => s.hydrateItem);

  const run = async (fn: () => Promise<unknown>, toast?: string): Promise<boolean> => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
      return false;
    }
    if (toast) showToast(toast);
    setPending(true);
    await hydrateItem(itemId).catch(() => {});
    setPending(false);
    setBusy(false);
    return true;
  };

  return { busy, pending, err, run };
}
