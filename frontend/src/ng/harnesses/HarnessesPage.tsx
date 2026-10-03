import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { isTextField } from "../keys";
import { AreaFrame } from "../templates/draft/AreaFrame";
import { Kv } from "../templates/panes/controls";
import { useConfigDraft, type ConfigDraft } from "../templates/draft/useConfigDraft";
import { showToast } from "../ui/Toast";
import { AreaPane } from "./AreaPane";
import { HarnessPane } from "./HarnessPane";
import { Lanes } from "./Lanes";
import { List, type Pick } from "./List";
import { ProfilePane } from "./ProfilePane";
import { ESCALATION_PATH, type HProblem, floorLanes, harnessLanes, profileLanes, resolvedOf } from "./model";
import { usePublishedPolicy } from "./usePublishedPolicy";
import { useProviders } from "./useProviders";
import { useRun } from "./ops";
import "./harnesses.css";

/** Settings › Harnesses (Decisions §11): one draft over harnesses.yaml and policy.yaml.
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
  const providers = useProviders();
  const published = usePublishedPolicy(view.draft);
  const [params, setParams] = useSearchParams();
  const [open, setOpen] = useState(params.has("harness") || params.has("profile"));
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

  const area = useMemo(() => ({
    crumb: "Harnesses",
    files: ["harnesses.yaml", "policy.yaml"],
    toast: "Published harnesses · new launches use it from now on",
    affects: () => (
      <>
        <Kv k="new launches" v="use the published access and profiles from now on" />
        <Kv k="running" v="sessions already launched keep the harness and model they started with" />
        <Kv k="repos" v="a repo's own narrowing is edited in Repos and still applies" />
      </>
    ),
  }), []);

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
  const common = { draft, r, providers, problems, changes, open, onCollapse: () => setOpen(false), onExpand: () => setOpen(true) };

  const addProfile = async () => {
    let name = "profile";
    for (let i = 2; r.profiles[name]; i++) name = `profile_${i}`;
    setAdding(true);
    const { ok } = await run({ op: "add_profile", name });
    setAdding(false);
    if (ok) go({ profile: name });
  };

  const crumbs = sel && (
    <>
      <span className="hn-crumb-sep" aria-hidden>›</span>
      {sel.kind === "profile" && <><span className="hn-crumb">Profiles</span><span className="hn-crumb-sep" aria-hidden>›</span></>}
      <span className="hn-crumb is-current">{sel.id}</span>
    </>
  );

  return (
    <AreaFrame draft={draft} area={area} pageKey="harnesses" title="Harnesses" tail={crumbs} onFix={onProblem}>
      {({ size, review, reserve, yaml }) => (
        <div className="hn-page">
          <EscUp active={!review && !!(sel || lane)} onEsc={up} />
          <List r={r} problems={problems} sel={sel} onPick={(p) => go(p.kind === "harness" ? { harness: p.id } : { profile: p.id })} onAdd={() => void addProfile()} />
          {addError && <p className="hn-error hn-toast" role="alert">{adding ? "" : addError}</p>}
          <div className="hn-stage">
            <div className="hn-canvas" style={{ right: reserve(open) }} onClick={up}>
              {missing ? (
                <p className="hn-empty">No {harness ? "harness" : "profile"} called {harness ?? profile}. <button type="button" className="hn-link" onClick={() => go({})}>Back to every harness</button></p>
              ) : (
                <Lanes lanes={lanes} pill={pill} selected={lane} empty={empty} problems={problems} onOpen={(k) => go(h ? { harness: h.id, lane: k } : prof ? { profile: profile!, lane: k } : { harness: k })} />
              )}
            </div>
            {!review && (h ? (
              <HarnessPane {...common} size={size} onYaml={yaml} h={h} lane={lane} onLane={(l) => go(l ? { harness: h.id, lane: l } : { harness: h.id })} onProfile={(name) => go({ profile: name })} />
            ) : prof ? (
              <ProfilePane {...common} size={size} onYaml={yaml} name={profile!} lane={lane} onLane={(l) => go(l ? { profile: profile!, lane: l } : { profile: profile! })} onHarness={(id) => go({ harness: id })} onGone={(to) => go(to ? { profile: to } : {})} />
            ) : (
              <AreaPane draft={draft} r={r} problems={problems} published={published} tab={tab} onTab={setTab} open={open} size={size} onCollapse={() => setOpen(false)} onExpand={() => setOpen(true)} onProblem={onProblem} />
            ))}
          </div>
        </div>
      )}
    </AreaFrame>
  );
}

/** Escape goes up one level (lane, then selection) unless a dialog or menu is open. */
function EscUp({ active, onEsc }: { active: boolean; onEsc: () => void }) {
  useEffect(() => {
    if (!active) return;
    const on = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isTextField(e.target) && !e.defaultPrevented && !document.querySelector("[role=dialog],[role=menu]")) onEsc();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [active, onEsc]);
  return null;
}
