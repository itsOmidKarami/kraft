import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import * as api from "../../api";
import "./templates.css";

/** `/templates/chains`: the `default` chain, else the first one (brief Decided 9). */
export function ChainsIndex() {
  const [ids, setIds] = useState<string[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    api.getTemplates().then((t) => setIds(t.map((c) => c.id))).catch(() => setFailed(true));
  }, []);
  if (failed) return <div className="tpl-note" role="alert">Couldn't load the chains. Reload to try again.</div>;
  if (!ids) return <div className="tpl-note">Loading chains…</div>;
  if (!ids.length) return <div className="tpl-note"><h1>No chains yet</h1></div>;
  return <Navigate replace to={`/templates/chains/${encodeURIComponent(ids.includes("default") ? "default" : ids[0])}`} />;
}
