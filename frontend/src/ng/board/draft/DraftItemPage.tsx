import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import * as api from "../../../api";
import { repoName } from "../../../format";
import { useStore } from "../../../store";
import type { ChainNode, Repo, TemplateSummary, Workspace } from "../../../types";
import { Inspector } from "../../graph/Inspector";
import type { ChainNode as GraphNode } from "../../graph/layout";
import { EDITOR_FIT } from "../../graph/camera";
import { StageGraph } from "../../graph/StageGraph";
import { useResizable, useWidth } from "../../graph/useResizable";
import { detailOf, jsonBody, request } from "../../http";
import { HeaderActions } from "../../shell/HeaderActions";
import { usePageItem } from "../../shell/pageItem";
import { Menu } from "../../ui/Menu";
import { Switch } from "../../ui/Switch";
import { showToast } from "../../ui/Toast";
import { Attach, CreateSplit, sayIfFiledPaused, type Created } from "../Composer";
import { draftBody, emptyDraft, nodeOverrides, type DraftState } from "./draftBody";
import "../board.css";
import "../../item/item.css";
import { sendOnModEnter } from "../../keys";

export const DRAFT_KEY = "kraft.ng.newItem";
type DryRun = { nodes: ChainNode[]; skipped: { node: string; why: string; kind?: string }[]; gates: string[]; caps: { budget_usd: number | null; budget_source: string; nodes: Record<string, { attempts: number; wall_clock_s: number }> } };
type Tab = "overview" | "config" | "yaml";

function loadDraft(state: unknown, q: URLSearchParams): DraftState {
  const carried = (state as { draft?: Partial<DraftState> } | null)?.draft;
  if (carried) return emptyDraft(carried);
  try {
    const saved = sessionStorage.getItem(DRAFT_KEY);
    // A bead in the URL is a fresh start, unless this draft already is that bead's (a reload keeps the edits).
    if (saved && (!q.get("bead") || JSON.parse(saved).bead === q.get("bead"))) return emptyDraft(JSON.parse(saved));
  } catch {
    // Private windows: nothing kept.
  }
  return emptyDraft({ title: q.get("title") ?? "", repo: q.get("repo") ?? "", chain: q.get("chain") ?? "", spec: q.get("spec") ?? "", plan: q.get("plan") ?? "", bead: q.get("bead") ?? "" });
}
const forget = () => { try { sessionStorage.removeItem(DRAFT_KEY); } catch { /* nothing kept */ } };

/** `/work-items/new` (W6 brief G): the item page for an item that does not
 *  exist yet. Nothing reaches the server but the dry run until Create. */
export function DraftItemPage() {
  const location = useLocation();
  const [q] = useSearchParams();
  const navigate = useNavigate();
  const [d, setD] = useState<DraftState>(() => loadDraft(location.state, q));
  const [repos, setRepos] = useState<Repo[]>([]);
  const [workspaces, setWorkspaces] = useState<Record<string, Workspace>>({});
  const [chains, setChains] = useState<TemplateSummary[]>([]);
  const [dry, setDry] = useState<{ run: DryRun } | { error: string } | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [paneOpen, setPaneOpen] = useState(true);
  const [ask, setAsk] = useState<{ to: string } | null>(null);
  const [addOpen, setAddOpen] = useState(!!(location.state as { members?: boolean } | null)?.members);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [frame, canvasW] = useWidth();
  const size = useResizable("draft", canvasW);
  const set = (p: Partial<DraftState>) => setD((x) => ({ ...x, ...p }));
  const dirty = !!(d.title.trim() || d.brief.trim());
  const setPageItem = usePageItem((s) => s.set);

  useEffect(() => {
    api.getRepos().then((r) => {
      const live = r.repos.filter((x) => x.enabled !== false);
      setRepos(live);
      setWorkspaces(r.workspaces ?? {});
      const first = live.find((x) => x.path === d.repo) ?? live[0];
      if (first) setD((x) => ({ ...x, repo: first.path, chain: x.chain || first.default_chain_template }));
    }).catch(() => {});
    api.getTemplates().then(setChains).catch(() => {});
    // Once, on arrival.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify(d)); } catch { /* nothing kept */ }
  }, [d]);
  useEffect(() => {
    setPageItem(d.repo ? { id: "new", repo: d.repo, title: d.title, bead_id: null } : null);
    return () => setPageItem(null);
  }, [d.repo, d.title, setPageItem]);

  const full = chains.find((c) => c.id === d.chain)?.nodes ?? [];
  const byId = (id: string) => repos.find((r) => r.id === id);
  const ws = Object.values(workspaces).find((w) => byId(w.root)?.path === d.repo) ?? null;
  const memberRows = ws ? Object.entries(ws.members).filter(([, m]) => byId(m.repository)).map(([id, m]) => ({ id, path: m.path, test: byId(m.repository)?.test_command ?? null })) : [];

  // B33, read again 300ms after anything that changes what runs.
  const key = JSON.stringify([d.repo, d.chain, d.spec, d.plan, d.skip, d.attempts, d.wallMin, d.nodeAttempts, d.nodeWallMin, d.autoEscalate, d.budget, d.members, d.pointer]);
  useEffect(() => {
    if (!d.repo || !d.chain || !full.length) return;
    const t = setTimeout(async () => {
      const body = { ...draftBody(d, full, ws?.id ?? null, false), title: d.title.trim() || "draft" };
      const r = await request<DryRun>("/work-items?dry_run=1", jsonBody("POST", body));
      setDry(r.status === 200 ? { run: r.body } : { error: detailOf(r.body) });
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, full.length]);

  const run = dry && "run" in dry ? dry.run : null;
  const covered = new Map((run?.skipped ?? []).filter((s) => s.why === "covered_by").map((s) => [s.node, s.kind ?? ""]));
  const skipped = (id: string) => covered.has(id) || d.skip.includes(id);
  const ran = full.filter((n) => !skipped(n.id));
  const gates = ran.filter((n) => n.kind === "gate").length;
  const runText = `${ran.length} of ${full.length} nodes run · ${gates} gate${gates === 1 ? "" : "s"}`;
  const nodes: GraphNode[] = useMemo(() => full.map((n) => ({
    id: n.id, kind: n.kind === "gate" ? "gate" : "exec", icon: (n.steps?.length ?? 0) > 1 ? "layers" : undefined, state: "plain",
    skipped: skipped(n.id), meta: covered.has(n.id) ? "covered" : d.skip.includes(n.id) ? "skipped" : undefined, metaTone: "amber",
  })), [full, run, d.skip]); // eslint-disable-line react-hooks/exhaustive-deps

  // Leaving with a title or brief asks first (Decisions §7b Leaving): the
  // crumbs, the sidebar, Cancel and Esc all come through here.
  const leave = (to: string) => (dirty ? setAsk({ to }) : go(to));
  const go = (to: string) => {
    forget();
    if (to.startsWith("/")) navigate(to);
    else if (/^https?:/.test(to)) window.location.assign(to);
  };
  const page = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      const a = (e.target as Element).closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a || !dirty || page.current?.contains(a) || a.target === "_blank") return;
      e.preventDefault();
      e.stopPropagation();
      setAsk({ to: a.getAttribute("href")! });
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, [dirty]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (ask) return setAsk(null);
      if (sel) return setSel(null);
      leave("/");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const create = async (autostart: boolean) => {
    if (!d.title.trim() || busy) return;
    setBusy(true);
    setError(null);
    const r = await request<Created>("/work-items", jsonBody("POST", draftBody(d, full, ws?.id ?? null, autostart)));
    setBusy(false);
    if (r.status !== 201 && r.status !== 200) return setError(detailOf(r.body));
    if (r.body.duplicate_warning) showToast(r.body.duplicate_warning, 6000);
    sayIfFiledPaused(autostart, r.body);
    forget();
    await useStore.getState().bootstrap().catch(() => {});
    navigate(`/?sel=${encodeURIComponent(r.body.id)}`);
  };

  const ok = !!d.title.trim() && !busy && !(dry && "error" in dry);
  const node = sel ? full.find((n) => n.id === sel) ?? null : null;
  const overrides = nodeOverrides(d, full);
  const configBadge = (d.autoEscalate ? 1 : 0) + (d.autoGate ? 0 : 1) + (d.budget.trim() ? 1 : 0) + (d.attempts.trim() ? 1 : 0) + (d.wallMin.trim() ? 1 : 0);
  const skipsOf = (k: "spec" | "plan") => {
    const ids = full.filter((n) => n.covered_by === k).map((n) => n.id);
    return ids.length ? `skips ${ids.join(", ")}` : "nothing in this chain to skip";
  };

  return (
    <div className="draft-page" ref={page}>
      <HeaderActions>
        <span className={`draft-tag${d.title.trim() ? " is-ok" : ""}`}>{d.title.trim() ? "DRAFT · NOT CREATED" : "ADD A TITLE TO CREATE"}</span>
        <button type="button" className="btn btn-secondary" onClick={() => leave("/")}>Cancel</button>
        <CreateSplit disabled={!ok} onCreate={create} />
      </HeaderActions>
      {error && <p className="item-error" role="alert">{error}</p>}
      <h1 className="board-visually-hidden">New work item</h1>
      <input className="draft-title" aria-label="Title" placeholder="Title" autoFocus value={d.title} onChange={(e) => set({ title: e.target.value })} />
      {d.bead && (
        <p className="draft-bead">
          Implements <code>{d.bead}</code>
          <button type="button" className="draft-bead-drop" aria-label={`Do not implement ${d.bead}`} onClick={() => set({ bead: "" })}>×</button>
        </p>
      )}
      <textarea className="draft-brief" aria-label="Brief" placeholder="Brief. Context, constraints, what done looks like. Every node reads it." value={d.brief} onChange={(e) => set({ brief: e.target.value })} onKeyDown={sendOnModEnter(() => create(true), ok)} />
      <div className="draft-chips">
        <span>Repo</span>
        <Menu
          label="Repo"
          triggerClass="composer-chip"
          trigger={<>{d.repo ? repoName(d.repo) : "repo"} <span aria-hidden className="board-caret">▾</span></>}
          items={repos.map((r) => ({ label: repoName(r.path), checked: r.path === d.repo, hint: r.default_chain_template, onSelect: () => set({ repo: r.path, chain: r.default_chain_template, skip: [], members: [], spec: "", plan: "" }) }))}
        />
        <span className="draft-gap" />
        <span>Start from existing</span>
        {(["spec", "plan"] as const).map((k) => (
          <span key={k} className="draft-slot">
            <Attach kind={k} repo={d.repo} path={d[k]} onPath={(p) => set({ [k]: p })} />
            {d[k] && <span className="draft-skips">{skipsOf(k)}</span>}
          </span>
        ))}
        {ws && memberRows.length > 0 && (
          <button type="button" className={d.members.length ? "composer-chip" : "composer-add"} onClick={() => { setSel(null); setTab("overview"); setPaneOpen(true); setAddOpen(true); }}>
            {d.members.length ? `${d.members.length} member${d.members.length === 1 ? "" : "s"} · chosen in the chain pane` : "+ members"}
          </button>
        )}
      </div>
      {dry && "error" in dry && <p className="item-error" role="alert">{dry.error}</p>}
      <div className="draft-canvas" ref={frame}>
        <StageGraph
          name={d.chain || "chain"}
          nodes={nodes}
          selected={sel ?? undefined}
          reserve={size.overlay ? 0 : paneOpen ? size.width : 40}
          // Opens readable, as the Chains editor does (W10 Decided 17), not at a 30% plain fit.
          fit={EDITOR_FIT}
          onSelect={(id) => { setSel(id); setTab((t) => (sel === id ? t : "overview")); }}
          onOpen={(id) => { setSel(id); setPaneOpen(true); }}
          onEscape={() => setSel(null)}
          onBackground={() => setSel(null)}
        />
        <div className="draft-canvas-head">
          <Menu
            label="Chain"
            triggerClass="composer-chip composer-mono draft-chain-btn"
            trigger={<>{d.chain || "chain"} <span aria-hidden className="board-caret">▾</span></>}
            items={chains.filter((c) => !c.error).map((c) => ({ label: c.id, checked: c.id === d.chain, hint: `${c.nodes.length} nodes · ${c.gates} gates${c.id === repos.find((r) => r.path === d.repo)?.default_chain_template ? " · repo default" : ""}`, onSelect: () => { set({ chain: c.id, skip: [] }); setSel(null); } }))}
          />
          <span>{runText}</span>
        </div>
        <Inspector
          id="draft-pane"
          open={paneOpen}
          size={size}
          crumbs={node ? [{ label: "new item", onClick: () => setSel(null) }, { label: d.chain, onClick: () => setSel(null) }] : [{ label: repoName(d.repo || "") || "repo" }, { label: "new item" }]}
          icon={node ? undefined : "workflow"}
          gate={node?.kind === "gate"}
          title={node ? node.id : d.chain || "chain"}
          sub={node ? `${node.kind === "gate" ? "gate" : "exec node"} · ${skipped(node.id) ? "skipped" : "will run"}` : `Draft item · ${runText}`}
          tabs={[{ value: "overview", label: "Overview" }, { value: "config", label: configBadge && !node ? `Config · ${configBadge}` : "Config" }, { value: "yaml", label: "YAML" }]}
          tab={tab}
          onTab={(t) => setTab(t as Tab)}
          onCollapse={() => setPaneOpen(false)}
          onExpand={() => setPaneOpen(true)}
        >
          {tab === "overview" && !node && (
            <ChainOverview d={d} set={set} full={full} runText={runText} skippedIds={full.filter((n) => skipped(n.id)).map((n) => n.id)} ws={ws} memberRows={memberRows} addOpen={addOpen} setAddOpen={setAddOpen} />
          )}
          {tab === "overview" && node && (
            <NodeOverview node={node} full={full} covered={covered.get(node.id) ?? null} skippedByYou={d.skip.includes(node.id)} onToggle={() => set({ skip: d.skip.includes(node.id) ? d.skip.filter((x) => x !== node.id) : [...d.skip, node.id] })} />
          )}
          {tab === "config" && !node && <ChainConfig d={d} set={set} caps={run?.caps ?? null} />}
          {tab === "config" && node && (
            <NodeConfig d={d} set={set} node={node} covered={covered.get(node.id) ?? null} cap={run?.caps.nodes[node.id] ?? null} />
          )}
          {tab === "yaml" && <Yaml chain={d.chain} rows={yamlRows(d, node ? { [node.id]: overrides[node.id] ?? {} } : overrides, node?.id ?? null)} />}
        </Inspector>
      </div>
      {ask && (
        <div className="draft-ask" role="alertdialog" aria-label="Discard this draft?">
          <b>Discard this draft?</b>
          <span>Nothing has been created. The title, brief and settings you entered are lost.</span>
          <div className="item-actions">
            <button type="button" className="btn btn-danger" onClick={() => { const to = ask.to; setAsk(null); go(to); }}>Discard</button>
            <button type="button" className="btn btn-secondary" autoFocus onClick={() => setAsk(null)}>Keep editing</button>
          </div>
        </div>
      )}
    </div>
  );
}

function ChainOverview({ d, set, full, runText, skippedIds, ws, memberRows, addOpen, setAddOpen }: {
  d: DraftState; set: (p: Partial<DraftState>) => void; full: ChainNode[]; runText: string; skippedIds: string[];
  ws: Workspace | null; memberRows: { id: string; path: string; test: string | null }[]; addOpen: boolean; setAddOpen: (o: boolean) => void;
}) {
  const [mq, setMq] = useState("");
  const attached = (["spec", "plan"] as const).filter((k) => d[k]).map((k) => `${k} · ${d[k]}`).join(", ");
  const picked = memberRows.filter((m) => d.members.includes(m.id));
  const open = memberRows.filter((m) => !d.members.includes(m.id) && m.path.toLowerCase().includes(mq.trim().toLowerCase()));
  return (
    <div className="draft-pane-body">
      <dl className="item-facts">
        <div><dt>repo</dt><dd>{repoName(d.repo)}</dd></div>
        <div><dt>chain</dt><dd>{d.chain} · {full.length} nodes</dd></div>
        <div><dt>will run</dt><dd>{runText}</dd></div>
        <div><dt>skipped</dt><dd>{skippedIds.join(", ") || "none"}</dd></div>
        <div><dt>attached</dt><dd>{attached || "nothing"}</dd></div>
        <div><dt>starts</dt><dd>paused. Nothing spends tokens until you start it.</dd></div>
      </dl>
      {ws && memberRows.length > 0 && (
        <section className="draft-section" aria-label="Repos in this item">
          <h3 className="draft-h">Repos in this item</h3>
          <div className="draft-repo"><span className="draft-repo-path">{repoName(d.repo)}</span><span className="draft-repo-sub">{picked.length ? "pointers only · branch and merge request" : "the whole item · branch and merge request"}</span><span className="draft-role">ROOT</span></div>
          {picked.map((m) => (
            <label key={m.id} className="draft-repo">
              <input type="checkbox" checked onChange={() => set({ members: d.members.filter((x) => x !== m.id) })} aria-label={`Drop ${m.path}`} />
              <span className="draft-repo-path">{m.path}</span><span className="draft-repo-sub">{m.test ? `${m.test} · ` : ""}branch and merge request</span><span className="draft-role">MEMBER</span>
            </label>
          ))}
          <button type="button" className="composer-add draft-add" aria-expanded={addOpen} onClick={() => setAddOpen(!addOpen)}>+ add a member</button>
          {addOpen && (
            <div className="draft-add-list">
              <input aria-label="Filter members" placeholder="Filter members" value={mq} onChange={(e) => setMq(e.target.value)} />
              {open.map((m) => (
                <button key={m.id} type="button" className="menu-item draft-add-row" onClick={() => { set({ members: [...d.members, m.id] }); setMq(""); }}>
                  <span className="composer-mono">{m.path}</span><span className="draft-repo-sub">{m.test ?? "no tests"}</span>
                </button>
              ))}
              {!open.length && <span className="item-muted">{memberRows.every((m) => d.members.includes(m.id)) ? "Every enabled member is in this item." : "No member matches."}</span>}
              <span className="item-muted">Only enabled child repos are listed. Enable more in Settings › Repos.</span>
            </div>
          )}
          <h3 className="draft-h">What happens</h3>
          <p className="draft-p">{picked.length
            ? `${picked.length + 1} repos get a branch and a merge request: ${picked.map((m) => m.path).join(", ")} and the root. Members merge first${d.pointer === "bump" ? ", then the root's pointers are bumped" : ", and the root is left unchanged"}.`
            : `Only ${repoName(d.repo)} gets a branch and a merge request. Add a member to file a cross-repo item.`}</p>
          {picked.length > 0 && (
            <fieldset className="draft-pointer">
              <legend className="draft-h">Root pointer</legend>
              {(["ignore", "bump"] as const).map((p) => (
                <label key={p} className="item-check"><input type="radio" name="pointer" checked={d.pointer === p} onChange={() => set({ pointer: p })} /> {p === "ignore" ? "Ignore · leave the root unchanged" : "Bump · update the root's pointers"}</label>
              ))}
            </fieldset>
          )}
        </section>
      )}
      <p className="item-muted">Click a node to look at it or skip it. Drag the canvas to pan.</p>
    </div>
  );
}

function NodeOverview({ node, full, covered, skippedByYou, onToggle }: { node: ChainNode; full: ChainNode[]; covered: string | null; skippedByYou: boolean; onToggle: () => void }) {
  const at = full.indexOf(node);
  return (
    <div className="draft-pane-body">
      <dl className="item-facts">
        <div><dt>kind</dt><dd>{node.kind === "gate" ? "gate · you decide" : `exec node${node.fix_loop ? " · fix loop" : ""}`}</dd></div>
        <div><dt>after</dt><dd>{full[at - 1]?.id ?? "—"}</dd></div>
        <div><dt>then</dt><dd>{full[at + 1]?.id ?? "—"}</dd></div>
        <div><dt>on this item</dt><dd className={covered || skippedByYou ? "draft-amber" : undefined}>{covered ? `skipped · covered by the attached ${covered}` : skippedByYou ? "skipped by you" : "will run"}</dd></div>
      </dl>
      <p className="item-muted">{node.kind === "gate" ? "A gate stops the run until you decide. Skip it to let the chain pass without a person." : "Click the canvas to go back to the chain."}</p>
      {!covered && <div className="item-actions"><button type="button" className="btn btn-secondary" onClick={onToggle}>{skippedByYou ? "Run this node" : "Skip on this item"}</button></div>}
    </div>
  );
}

function Row({ label, hint, children, dim }: { label: string; hint: string; children: React.ReactNode; dim?: boolean }) {
  return (
    <div className={`draft-row${dim ? " is-dim" : ""}`}>
      <div className="draft-row-text"><span className="draft-row-label">{label}</span><span className="draft-row-hint">{hint}</span></div>
      {children}
    </div>
  );
}
const field = (label: string, value: string, ph: string, on: (v: string) => void, disabled?: boolean) => (
  <input className="draft-input" aria-label={label} placeholder={ph} value={value} disabled={disabled} inputMode="decimal" onChange={(e) => on(e.target.value)} />
);

function ChainConfig({ d, set, caps }: { d: DraftState; set: (p: Partial<DraftState>) => void; caps: DryRun["caps"] | null }) {
  const loop = caps ? Object.values(caps.nodes)[0] : undefined;
  return (
    <div className="draft-pane-body">
      <h3 className="draft-h">Overrides for this item</h3>
      <Row label="Auto-escalate every gate" hint="Gates with a reviewer escalate before you see them."><Switch label="Auto-escalate every gate" checked={d.autoEscalate} onChange={(v) => set({ autoEscalate: v })} /></Row>
      <Row label="Agent reviews first" hint="An agent reads an escalation before it reaches you."><Switch label="Agent reviews first" checked={d.autoGate} onChange={(v) => set({ autoGate: v })} /></Row>
      <Row label="Budget" hint="Blank uses the Policy default.">{field("Budget", d.budget, caps?.budget_usd != null ? `$${caps.budget_usd.toFixed(2)} · ${caps.budget_source}` : "policy", (v) => set({ budget: v }))}</Row>
      <Row label="Fix attempts" hint="Every fix-loop node without its own value.">{field("Fix attempts", d.attempts, loop ? `${loop.attempts} · policy` : "policy", (v) => set({ attempts: v }))}</Row>
      <Row label="Wall clock (min)" hint="Every fix-loop node without its own value.">{field("Wall clock (min)", d.wallMin, loop ? `${Math.round(loop.wall_clock_s / 60)} · policy` : "policy", (v) => set({ wallMin: v }))}</Row>
      <p className="item-muted">Blank uses the Policy default. Set a node's own value on that node.</p>
    </div>
  );
}

function NodeConfig({ d, set, node, covered, cap }: { d: DraftState; set: (p: Partial<DraftState>) => void; node: ChainNode; covered: string | null; cap: { attempts: number; wall_clock_s: number } | null }) {
  const off = d.skip.includes(node.id);
  return (
    <div className="draft-pane-body">
      <h3 className="draft-h">This node, on this item</h3>
      <Row label="Skip on this item" hint={covered ? `Covered by the attached ${covered}. Remove it to run this node.` : off ? "The chain continues past this node. Nothing in it spends tokens." : "Runs as the chain says."} dim={!!covered}>
        <Switch label="Skip on this item" checked={!!covered || off} disabled={!!covered} onChange={(v) => set({ skip: v ? [...d.skip, node.id] : d.skip.filter((x) => x !== node.id) })} />
      </Row>
      {node.fix_loop && (
        <>
          <Row label="Fix attempts" hint="This node only." dim={off}>{field("Fix attempts, this node", d.nodeAttempts[node.id] ?? "", d.attempts ? `${d.attempts} · item` : cap ? `${cap.attempts} · policy` : "policy", (v) => set({ nodeAttempts: { ...d.nodeAttempts, [node.id]: v } }), off)}</Row>
          <Row label="Wall clock (min)" hint="This node only." dim={off}>{field("Wall clock (min), this node", d.nodeWallMin[node.id] ?? "", d.wallMin ? `${d.wallMin} · item` : cap ? `${Math.round(cap.wall_clock_s / 60)} · policy` : "policy", (v) => set({ nodeWallMin: { ...d.nodeWallMin, [node.id]: v } }), off)}</Row>
          <p className="item-muted">A value here wins over the item-wide value.</p>
        </>
      )}
    </div>
  );
}

/** The draft's own settings, as plain rows under the chain's YAML (R54: no client YAML emitter). */
function yamlRows(d: DraftState, overrides: Record<string, Record<string, unknown>>, node: string | null): [string, string][] {
  const rows: [string, string][] = [];
  const skip = node ? d.skip.filter((x) => x === node) : d.skip;
  if (skip.length) rows.push(["skipped", skip.join(", ")]);
  for (const [id, o] of Object.entries(overrides)) {
    const parts = [o.auto_escalate ? "auto-escalate" : "", o.attempts != null ? `${o.attempts} fix attempts` : "", o.wall_clock_s != null ? `wall clock ${Math.round(Number(o.wall_clock_s) / 60)} min` : ""].filter(Boolean);
    if (parts.length) rows.push([id, parts.join(" · ")]);
  }
  if (!node) {
    for (const k of ["spec", "plan"] as const) if (d[k]) rows.push([`attached ${k}`, d[k]]);
    if (d.budget.trim()) rows.push(["budget", d.budget.trim()]);
    if (!d.autoGate) rows.push(["agent reviews first", "off"]);
    if (d.members.length) rows.push(["members", `${d.members.join(", ")} · root pointer ${d.pointer}`]);
  }
  return rows;
}

function Yaml({ chain, rows }: { chain: string; rows: [string, string][] }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    if (!chain) return;
    setText(null);
    api.getTemplate(chain).then((f) => setText(f.text), () => setText(""));
  }, [chain]);
  return (
    <div className="draft-pane-body">
      <pre className="draft-yaml" aria-label={`${chain} chain YAML`}>{text ?? "Loading…"}</pre>
      <p className="item-muted">The chain as published, read-only. This draft's own settings, applied on top:</p>
      {rows.length ? (
        <dl className="item-facts">{rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      ) : <p className="item-muted">Nothing changed. This item runs the chain and policy as published.</p>}
    </div>
  );
}
