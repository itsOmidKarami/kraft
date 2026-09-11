import { useState } from "react";
import { showToast } from "../../../components/Toast";
import { useStore } from "../../../store";

/** The shared submit/busy/error/toast wrapper every composer and every
 *  plain-click button in the action bar uses. */
export function useActionBar(itemId: string) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const hydrateItem = useStore((s) => s.hydrateItem);

  const run = async (fn: () => Promise<unknown>, toast?: string) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      hydrateItem(itemId).catch(() => {});
      if (toast) showToast(toast);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return { busy, err, run };
}
