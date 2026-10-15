import { offlineWords, reload, useOffline } from "../offline";

/** Over every page: the list read failed or the live connection dropped. */
export function OfflineBanner() {
  const { offline, loadError } = useOffline();
  if (!offline) return null;
  return (
    <div className="ng-offline" role="alert">
      <span className="ng-offline-mark" aria-hidden>!</span>
      <span className="ng-offline-text">{offlineWords(loadError)}</span>
      <button type="button" className="btn btn-danger" onClick={() => void reload().catch(() => {})}>Retry now</button>
    </div>
  );
}
