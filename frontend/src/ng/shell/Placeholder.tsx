import { useLocation } from "react-router-dom";
import { legacyPath } from "../legacyPath";

/** A page no wave has built yet: it names itself and links to its shipped page. */
export function Placeholder({ label, note = "This page is not in the new UI yet." }: { label: string; note?: string }) {
  const { pathname, search } = useLocation();
  return (
    <div className="ng-placeholder">
      <h1>{label}</h1>
      <p>{note}</p>
      <a href={legacyPath({ pathname: `/ng${pathname}`, search })}>Open it on the current UI ↗</a>
    </div>
  );
}
