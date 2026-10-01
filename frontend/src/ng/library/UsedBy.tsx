import { Link } from "react-router-dom";
import { Head, Note } from "../templates/panes/controls";
import type { Use } from "./types";

/** A chain's node view: the node is the path's first segment (W10 selects by node only, Decided 10). */
export const useUrl = (u: Use) => `/templates/chains/${encodeURIComponent(u.chain)}/nodes/${encodeURIComponent(u.path.split(".")[0])}`;

/** Who uses a component, from the published library: the chain, where in it, whether the use sets keys of its own. */
export function UsedBy({ uses }: { uses: Use[] | null | undefined }) {
  if (uses === null || uses === undefined) return null;
  return (
    <>
      <Head>Used by</Head>
      {!uses.length && <Note>Not used by any chain.</Note>}
      {uses.map((u) => (
        <div key={`${u.chain}|${u.path}`} className="lib-use">
          <Link className="lib-use-chain" to={useUrl(u)}>{u.chain}</Link>
          <span className="lib-use-path">{u.path}</span>
          {u.overrides && <span className="lib-chip">overrides</span>}
          {u.via && <span className="lib-chip">via {u.via}</span>}
        </div>
      ))}
    </>
  );
}
