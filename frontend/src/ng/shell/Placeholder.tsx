import { Link } from "react-router-dom";

/** A page with nothing to show (an unknown address, a missing item): it
 *  names itself and links back to the board. */
export function Placeholder({ label, note = "There is no page at this address." }: { label: string; note?: string }) {
  return (
    <div className="ng-placeholder">
      <h1>{label}</h1>
      <p>{note}</p>
      <Link to="/">Back to the board</Link>
    </div>
  );
}
