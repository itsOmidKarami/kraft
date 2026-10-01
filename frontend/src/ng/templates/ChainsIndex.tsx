import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import * as api from "../../api";
import { detailOf } from "../http";
import { draftsChanged, postOps } from "./draft/draftApi";
import { IdRow } from "./menus/IdRow";
import "./templates.css";

/** `/templates/chains`: the `default` chain, else the first one; with none,
 *  New chain (brief Decided 9). */
export function ChainsIndex() {
  const [ids, setIds] = useState<string[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const navigate = useNavigate();
  useEffect(() => {
    api.getTemplates().then((t) => setIds(t.map((c) => c.id))).catch(() => setFailed(true));
  }, []);
  if (failed) return <div className="tpl-note" role="alert">Couldn't load the chains. Reload to try again.</div>;
  if (!ids) return <div className="tpl-note">Loading chains…</div>;
  if (!ids.length)
    return (
      <div className="tpl-note tpl-first">
        <h1>No chains yet</h1>
        <p>A chain is the steps every work item runs. Start one:</p>
        <IdRow label="New chain id" taken={[]} go="Create →" refused={refused} onGo={async (id) => {
          const a = await postOps("chains", id, [{ op: "new_chain" }]);
          if (a.status !== 200) return setRefused(detailOf(a.body));
          draftsChanged();
          navigate(`/templates/chains/${encodeURIComponent(id)}`);
        }} />
      </div>
    );
  return <Navigate replace to={`/templates/chains/${encodeURIComponent(ids.includes("default") ? "default" : ids[0])}`} />;
}
