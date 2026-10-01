import { useParams } from "react-router-dom";
import { useConfigDraft } from "./draft/useConfigDraft";
import "./templates.css";

/** The Chains editor (Decisions §9): one chain's draft on a canvas. */
export function ChainsPage() {
  const { chain = "" } = useParams();
  const draft = useConfigDraft("chains", chain);
  if (draft.status === "notFound") return <div className="tpl-note"><h1>No chain called {chain}</h1></div>;
  if (!draft.view) return <div className="tpl-note">Loading {chain}…</div>;
  return <div className="tpl-page" />;
}
