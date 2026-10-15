import { Link, useLocation } from "react-router-dom";
import { nearestPage } from "../../shell/pageName";
import { ScreenHeader } from "./ScreenHeader";

/** An address no phone screen answers. The tab bar stays, and the way up is the area's list, or More when only the section is known. */
export function NotFound() {
  const { pathname } = useLocation();
  const near = nearestPage(pathname);
  const up = near.to === "/" ? null : near.section ? { to: "/more", label: "More" } : near;
  return (
    <>
      <ScreenHeader />
      <div className="ph-content">
        <h1 className="ph-title">Not found</h1>
        <p className="ph-empty">There is no screen at this address.</p>
        {up && <Link className="ph-link" to={up.to}>Up to {up.label}</Link>}
        <Link className="ph-link" to="/">Back to the board</Link>
      </div>
    </>
  );
}
