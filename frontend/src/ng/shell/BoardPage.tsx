import { useEffect, useState } from "react";
import * as api from "../../api";
import { FirstRun } from "./FirstRun";
import { Placeholder } from "./Placeholder";

/** The Board route until a wave builds the board: first-run when no repo is connected, else the stub.
 *  Decided once on load, so connecting a repo from first-run does not swap the page away mid-setup.
 *  A failed or pending load shows the stub: an empty list is the only thing that means "no repo". */
export function BoardPage({ label }: { label: string }) {
  const [empty, setEmpty] = useState(false);
  useEffect(() => {
    api.getRepos().then((r) => setEmpty(r.repos.length === 0)).catch(() => {});
  }, []);
  return empty ? <FirstRun /> : <Placeholder label={label} />;
}
