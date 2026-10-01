import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useResizable } from "../graph/useResizable";
import { isTextField } from "../keys";
import { HeaderTail } from "../shell/HeaderActions";
import { useConfigDraft, type ConfigDraft } from "../templates/draft/useConfigDraft";
import { counts } from "../templates/draft/view";
import { showToast } from "../ui/Toast";
import { AreaPane } from "./AreaPane";
import { HarnessPane } from "./HarnessPane";
import { Lanes } from "./Lanes";
import { List, type Pick } from "./List";
import { ProfilePane } from "./ProfilePane";
import { ESCALATION_PATH, type HProblem, floorLanes, harnessLanes, profileLanes, resolvedOf } from "./model";
import { useBox } from "./useBox";
import { usePublishedPolicy } from "./usePublishedPolicy";
import { useProviders } from "./useProviders";
import { useRun } from "./ops";
import "./harnesses.css";

/** Templates › Harnesses (Decisions §11): one draft over harnesses.yaml and policy.yaml.
 *  The selection is in the query (`?harness=` / `?profile=`, `&lane=`), so no id shadows a route. */
export function HarnessesPage() {
  const draft = useConfigDraft("harnesses", "harnesses");
  if (draft.status === "error" && !draft.view) return <div className="hn-note" role="alert">Couldn't load the harnesses. {draft.error}</div>;
  if (!draft.view) return <div className="hn-note">Loading the harnesses…</div>;
  return <Editor draft={draft} />;
}

function Editor({ draft }: { draft: ConfigDraft }) {
  const view = draft.view!;
  const result = view.result;
  const r = resolvedOf(result);
  const problems = result.problems as HProblem[];
  const n = counts(result);
  const providers = useProviders();
  const published = usePublishedPolicy(view.draft);
  const [params, setParams] = useSearchParams();
  const [frame, canvasW] = useBox();
  const size = useResizable("harnesses", canvasW);
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState("overview");
  const { run, error: addError } = useRun(draft);
  const [adding, setAdding] = useState(false);

  const harness = params.get("harness");
  const profile = params.get("profile");
  const lane = params.get("lane");
  const sel: Pick | null = harness ? { kind: "harness", id: harness } : profile ? { kind: "profile", id: profile } : null;

  const go = useCallback((next: { harness?: string; profile?: string; lane?: string }) => {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(next)) if (v) p.set(k, v);
    setParams(p);
    setOpen(true);
  }, [setParams]);
  const up = useCallback(() => {
    if (lane) go(harness ? { harness } : { profile: profile! });
    else if (sel) go({});
  }, [lane, harness, profile, sel, go]);

  useEffect(() => {
    if (draft.error) showToast(draft.error);
  }, [draft.error]);

  // ⌘Z undoes the last draft request outside a text field (brief B.5); Escape goes up one level.
  const undo = draft.undo;
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (isTextField(e.target)) return;
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "z") {
        e.preventDefault();
        undo();
      } else if (e.key === "Escape" && !e.defaultPrevented && !document.querySelector("[role=dialog],[role=menu]") && (sel || lane)) {
        up();
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [undo, up, sel, lane]);

  if (!r) return <div className="hn-note" role="alert">The harnesses draft has nothing to show{result.yaml_error ? `: ${result.yaml_error.file} line ${result.yaml_error.line}: ${result.yaml_error.message}` : "."}</div>;

  const changes = new Set(result.changes.map((c) => c.path));
  const h = harness ? r.harnesses.find((x) => x.id === harness) : undefined;
  const prof = profile ? r.profiles[profile] : undefined;
  const missing = (harness && !h) || (profile && !prof);

  const lanes = h ? harnessLanes(r, h.id) : prof ? profileLanes(r, profile!) : floorLanes(r, problems);
  const pill = h ? `one lane per profile its tasks select · ${h.tasks.filter((t) => !t.fallback).length} tasks` : prof ? "one lane per provider this profile names" : "every harness · click one, or a lane";
  const empty = h ? "No task selects this harness." : prof ? "This profile names no provider yet." : "No harnesses in harnesses.yaml.";

  const onProblem = (p: HProblem) => {
    if (p.path === ESCALATION_PATH) {
      go({});
      setTab("config");
      return;
    }
    const hit = r.harnesses.find((x) => x.tasks.some((t) => t.chain === p.chain && t.path === p.path));
    if (p.profile && r.profiles[p.profile]) go({ profile: p.profile });
    else if (hit) go({ harness: hit.id });
  };
  const common = { draft, r, providers, problems, changes, open, size, onCollapse: () => setOpen(false), onExpand: () => setOpen(true) };

  const addProfile = async () => {
    let name = "profile";
    for (let i = 2; r.profiles[name]; i++) name = `profile_${i}`;
    setAdding(true);
    const { ok } = await run({ op: "add_profile", name });
    setAdding(false);
    if (ok) go({ profile: name });
  };

  return (
    <div className="hn-page" ref={frame}>
      <h1 className="hn-sr">Harnesses</h1>
      <HeaderTail>
        {sel && (
          <>
            <span className="hn-crumb-sep" aria-hidden>›</span>
            {sel.kind === "profile" && <><span className="hn-crumb">Profiles</span><span className="hn-crumb-sep" aria-hidden>›</span></>}
            <span className="hn-crumb is-current">{sel.id}</span>
          </>
        )}
        {view.draft ? <span className="hn-draft">DRAFT · {n.changes} {n.changes === 1 ? "CHANGE" : "CHANGES"}</span> : <span className="hn-published">published</span>}
        {n.problems > 0 && (
          <button type="button" className="hn-problems-badge" title="Show the problems" onClick={() => { go({}); setTab("overview"); }}>
            {n.problems} {n.problems === 1 ? "PROBLEM" : "PROBLEMS"}
          </button>
        )}
      </HeaderTail>
      <List r={r} problems={problems} sel={sel} onPick={(p) => go(p.kind === "harness" ? { harness: p.id } : { profile: p.id })} onAdd={() => void addProfile()} />
      {addError && <p className="hn-error hn-toast" role="alert">{adding ? "" : addError}</p>}
      <div className="hn-stage">
        <div className="hn-canvas" style={{ paddingRight: open ? size.width + 20 : 60 }} onClick={up}>
          {missing ? (
            <p className="hn-empty">No {harness ? "harness" : "profile"} called {harness ?? profile}. <button type="button" className="hn-link" onClick={() => go({})}>Back to every harness</button></p>
          ) : (
            <Lanes lanes={lanes} pill={pill} selected={lane} empty={empty} problems={problems} onOpen={(k) => go(h ? { harness: h.id, lane: k } : prof ? { profile: profile!, lane: k } : { harness: k })} />
          )}
        </div>
        {h ? (
          <HarnessPane {...common} h={h} lane={lane} onLane={(l) => go(l ? { harness: h.id, lane: l } : { harness: h.id })} onProfile={(name) => go({ profile: name })} />
        ) : prof ? (
          <ProfilePane {...common} name={profile!} lane={lane} onLane={(l) => go(l ? { profile: profile!, lane: l } : { profile: profile! })} onHarness={(id) => go({ harness: id })} onGone={(to) => go(to ? { profile: to } : {})} />
        ) : (
          <AreaPane draft={draft} r={r} problems={problems} published={published} tab={tab} onTab={setTab} open={open} size={size} onCollapse={() => setOpen(false)} onExpand={() => setOpen(true)} onProblem={onProblem} />
        )}
      </div>
    </div>
  );
}
