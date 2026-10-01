import { Link } from "react-router-dom";
import { RootHeader } from "./ScreenHeader";

/** An address no phone screen answers. */
export function NotFound() {
  return (
    <>
      <RootHeader title="Not found" />
      <div className="ph-content">
        <h1 className="ph-title">Not found</h1>
        <p className="ph-empty">There is no screen at this address.</p>
        <Link className="ph-link" to="/">Back to the board</Link>
      </div>
    </>
  );
}
