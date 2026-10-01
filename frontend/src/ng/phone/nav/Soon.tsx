import { useLocation } from "react-router-dom";
import { legacyPath } from "../../legacyPath";
import { RootHeader, ScreenHeader } from "./ScreenHeader";
import { parentOf } from "./route";

/** A screen no section has built yet (W17 brief A.9): it names itself and links to its shipped page. */
export function Soon({ title }: { title: string }) {
  const { pathname, search } = useLocation();
  const root = parentOf(pathname + search) === null;
  return (
    <>
      {root ? <RootHeader title={title} /> : <ScreenHeader />}
      <div className="ph-content ph-soon">
        {!root && <h1 className="ph-title">{title}</h1>}
        <p>This screen is on the way.</p>
        <a className="ph-link" href={legacyPath({ pathname: `/ng${pathname}`, search })}>Open it on the current UI ↗</a>
      </div>
    </>
  );
}
