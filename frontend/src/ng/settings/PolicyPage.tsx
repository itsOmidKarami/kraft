import { useEffect, useMemo, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { Workflow } from "lucide-react";
import { request } from "../http";
import { Button } from "../ui/Button";
import { Tabs } from "../ui/Tabs";
import { AreaFrame } from "../templates/draft/AreaFrame";
import { useConfigDraft, type ConfigDraft } from "../templates/draft/useConfigDraft";
import type { Problem } from "../templates/draft/types";
import { Kv } from "../templates/panes/controls";
import type { Ctx } from "./policy/ctx";
import { Housekeeping } from "./policy/Housekeeping";
import { Limits } from "./policy/Limits";
import { Loops } from "./policy/Loops";
import { PreviewPane } from "./policy/PreviewPane";
import { SECTIONS, sectionOfKey, sectionOfProblem, type Section } from "./policy/sections";
import { limitLabel } from "./policy/keys";
import { policyOf } from "./policy/types";
import "./settings.css";
import "./policy/policy.css";
import { usePane } from "../shell/usePane";

const isSection = (s: string | undefined): s is Section => SECTIONS.some((x) => x.id === s);
export const policyUrl = (s: Section) => `/settings/policy/${s}`;

/** `/settings/policy[/:section]` (Decisions §12): one page of values that edit in place, the section picked on the tab strip. */
export function PolicyPage() {
  const { section } = useParams();
  const draft = useConfigDraft("policy", "policy");
  if (!isSection(section)) return <Navigate to={policyUrl("limits")} replace />;
  if (draft.status === "notFound" || (draft.status === "error" && !draft.view))
    return <div className="tpl-note" role="alert"><h1>Could not load policy</h1><Button onClick={() => void draft.reload()}>Retry</Button></div>;
  if (!draft.view) return <div className="tpl-note" aria-busy="true">Loading policy…</div>;
  return <Editor draft={draft} section={section} />;
}

function Editor({ draft, section }: { draft: ConfigDraft; section: Section }) {
  const navigate = useNavigate();
  const r = draft.view!.result;
  const p = policyOf(r);
  const [active, setActive] = useState<number | null>(null);
  const [chains, setChains] = useState<string[]>([]);
  const [preview, setPreview] = useState(false);
  usePane(preview && section === "limits");
  const [flash, setFlash] = useState<string | null>(null);
  const stamp = draft.view!.files["policy.yaml"];

  useEffect(() => {
    void request<{ id: string }[]>("/templates/chains").then((a) => { if (a.status === 200 && Array.isArray(a.body)) setChains(a.body.map((c) => c.id)); });
  }, []);
  // The slot line's count: what is running now, read again after each write.
  useEffect(() => {
    let live = true;
    void request<{ active_count?: number }>("/policy").then((a) => { if (live && a.status === 200) setActive(a.body.active_count ?? null); });
    return () => void (live = false);
  }, [stamp]);
  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => setFlash(null), 1600);
    return () => clearTimeout(t);
  }, [flash]);

  const bySection = useMemo(() => {
    const out: Record<Section, { draft: boolean; problem: boolean }> = { limits: { draft: false, problem: false }, loops: { draft: false, problem: false }, housekeeping: { draft: false, problem: false } };
    for (const c of r.changes) { const s = sectionOfKey(c.path); if (s) out[s].draft = true; }
    for (const x of r.problems) { const s = sectionOfProblem(x); if (s) out[s].problem = true; }
    return out;
  }, [r.changes, r.problems]);

  const tabs = SECTIONS.map((s) => {
    const m = bySection[s.id];
    return { value: s.id, label: s.label, dot: m.problem ? "problem" as const : m.draft ? "draft" as const : undefined };
  });

  const area = useMemo(() => ({
    crumb: "Policy",
    files: ["policy.yaml"],
    toast: "Published policy · applies to items filed from now",
    label: limitLabel,
    affects: () => (
      <>
        <Kv k="new items" v="use the published policy from now on" />
        <Kv k="running" v={`${active ?? 0} ${active === 1 ? "item keeps" : "items keep"} the policy they froze at intake`} />
        <Kv k="housekeeping" v="takes effect at the next restart" />
      </>
    ),
  }), [active]);

  const onFix = (x: Problem) => {
    const s = sectionOfProblem(x) ?? "limits";
    navigate(policyUrl(s));
    if (x.level) setFlash(x.level);
  };
  const changes = useMemo(() => new Map(r.changes.map((c) => [c.path, c])), [r.changes]);
  const ctx: Ctx | null = p ? { draft, p, changes, problems: r.problems, active } : null;
  // Preview reads the server's below lists: an older server sends none, and there is nothing to preview.
  const previewable = !!p && "below" in (Object.values(p.limits.caps)[0] ? Object.values(Object.values(p.limits.caps)[0])[0] : {});

  return (
    <AreaFrame
      draft={draft}
      area={area}
      pageKey="policy"
      title="Policy"
      onFix={onFix}
    >
      {({ size, review, reserve }) => (
        <>
          <div className="pol-body" data-flash={flash ?? undefined} style={{ right: reserve(preview && section === "limits") }}>
            <div className="pol-page">
              <Tabs label="Policy section" id="policy" tabs={tabs} value={section} onChange={(s) => navigate(policyUrl(s))} />
              <div id="policy-panel" role="tabpanel" aria-labelledby={`policy-tab-${section}`} className="pol-panel">
              {!ctx ? (
                <div className="tpl-note" role="alert">
                  <h1>policy.yaml does not load</h1>
                  {r.problems[0] && <p>{r.problems[0].message}</p>}
                  <p>Open YAML in the header to repair it.</p>
                </div>
              ) : section === "limits" ? <Limits ctx={ctx} preview={previewable && <Button aria-pressed={preview} onClick={() => setPreview((v) => !v)}><Workflow size={13} aria-hidden /> Preview on a chain</Button>} /> : section === "loops" ? <Loops ctx={ctx} /> : <Housekeeping ctx={ctx} />}
              </div>
            </div>
          </div>
          {!review && preview && section === "limits" && ctx && <PreviewPane draft={draft} chains={chains} size={size} onClose={() => setPreview(false)} />}
        </>
      )}
    </AreaFrame>
  );
}
