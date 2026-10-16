import { Button } from "../../ui/Button";
import { offlineWords, reload, useOffline, useReloadOnReconnect } from "../../offline";

/** Over every screen: the list read failed or the live connection dropped. */
export function OfflineBanner() {
  const { offline, loadError } = useOffline();
  useReloadOnReconnect();
  if (!offline) return null;
  return (
    <div className="ph-offline-banner" role="alert">
      <p>{offlineWords(loadError)}</p>
      <Button className="ph-btn" onClick={() => void reload().catch(() => {})}>Retry now</Button>
    </div>
  );
}
