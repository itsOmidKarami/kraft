import { Link } from "react-router-dom";
import "./ui.css";

/** An address that names a thing that isn't there: its id in mono, and the way back to where such things are listed. */
export function NotFound({ what, id, backTo, backLabel }: { what: string; id: string; backTo: string; backLabel: string }) {
  return (
    <div className="not-found" role="status">
      <h1>No {what} called <code>{id}</code></h1>
      <p>It may have been removed or renamed.</p>
      <Link to={backTo}>{backLabel}</Link>
    </div>
  );
}
