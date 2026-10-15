import { Link, useLocation } from "react-router-dom";
import { nearestPage } from "./pageName";

/** A page with nothing to show (an unknown address, a missing item): it
 *  names itself and links to the section above it, then to the board. */
export function Placeholder({ label, note = "There is no page at this address." }: { label: string; note?: string }) {
  const up = nearestPage(useLocation().pathname);
  return (
    <div className="ng-placeholder">
      <h1>{label}</h1>
      <p>{note}</p>
      {up.to !== "/" && <Link to={up.to}>Up to {up.label}</Link>}
      <Link to="/">Back to the board</Link>
    </div>
  );
}
