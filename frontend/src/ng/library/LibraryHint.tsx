import { Link, useInRouterContext } from "react-router-dom";
import { refUrl } from "./types";

/** "In the library: tasks.implementer" on a chain component that extends one (R47): a link to that component. */
export function LibraryHint({ section, name }: { section: "nodes" | "tasks"; name: string }) {
  const inRouter = useInRouterContext();
  const id = `${section}.${name}`;
  return (
    <p className="lib-hint">
      In the library:{" "}
      {inRouter ? <Link className="lib-hint-link" to={refUrl(id)}>{id}</Link> : <span className="lib-hint-link">{id}</span>}
    </p>
  );
}
