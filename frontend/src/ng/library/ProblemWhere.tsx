import { Link } from "react-router-dom";
import type { Problem } from "../templates/draft/types";
import { ROUTES } from "../shell/routes";

const reposBuilt = () => ROUTES.some((r) => r.path === "/settings/repos" && r.built);

/** Where a library problem breaks (Decisions §10 Problems): the chain, or the repository, and the library
 *  component it comes from. A problem in `library.yaml` itself names none of the first two. */
export function ProblemWhere({ p }: { p: Problem }) {
  if (!p.chain && !p.repo && !p.component) return null;
  return (
    <span className="lib-where">
      {p.chain && <>breaks chain <Link className="lib-where-link" to={`/templates/chains/${encodeURIComponent(p.chain)}`}>{p.chain}</Link></>}
      {p.repo && <>breaks repo {reposBuilt() ? <Link className="lib-where-link" to="/settings/repos">{p.repo}</Link> : <span className="lib-where-name">{p.repo}</span>}</>}
      {p.component && <>{p.chain || p.repo ? " · " : ""}from <span className="lib-where-name">{p.component}</span></>}
    </span>
  );
}
