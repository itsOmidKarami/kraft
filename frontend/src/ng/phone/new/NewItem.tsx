import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import * as api from "../../../api";
import { repoName } from "../../../format";
import { useStore } from "../../../store";
import type { Repo, SearchResult, TemplateSummary } from "../../../types";
import { detailOf, jsonBody, request } from "../../http";
import { Button } from "../../ui/Button";
import { showToast } from "../../ui/Toast";
import { ChoiceSheet, ConfirmSheet, EditSheet, useSheet } from "../nav/Sheet";
import { useBack } from "../nav/trail";
import { createBody, loadDraft, looksLikePath, saveDraft, type AttachKind, type NewDraft } from "./body";
import "./new.css";

/** `/work-items/new` (W17 brief G): title, brief, repo, chain, an optional spec and plan, and Create paused or Create and start (GAP §5.1). */
export function NewItem() {
  const back = useBack();
  const navigate = useNavigate();
  const sheet = useSheet();
  const [params] = useSearchParams();
  // A bead found in Search arrives as ?title=, prefilled only into an empty draft.
  const [d, setD] = useState<NewDraft>(() => { const x = loadDraft(); return x.title || x.brief ? x : { ...x, title: params.get("title") ?? "" }; });
  const [repos, setRepos] = useState<Repo[]>([]);
  const [chains, setChains] = useState<TemplateSummary[]>([]);
  const [kind, setKind] = useState<AttachKind>("spec");
  const [hits, setHits] = useState<SearchResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (p: Partial<NewDraft>) => setD((x) => ({ ...x, ...p }));
  const dirty = !!(d.title.trim() || d.brief.trim());

  useEffect(() => saveDraft(d), [d]);
  useEffect(() => {
    api.getRepos().then((r) => {
      const live = r.repos.filter((x) => x.enabled !== false);
      setRepos(live);
      setD((x) => (x.repo || !live[0] ? x : { ...x, repo: live[0].path, chain: live[0].default_chain_template }));
    }).catch(() => {});
    api.getTemplates().then(setChains).catch(() => {});
  }, []);

  const repo = repos.find((r) => r.path === d.repo);
  const create = async (autostart: boolean) => {
    if (!d.title.trim() || busy) return;
    setBusy(true);
    setError(null);
    const r = await request<{ id: string; bead_id?: string | null; duplicate_warning?: string }>("/work-items", jsonBody("POST", createBody(d, autostart)));
    setBusy(false);
    if (r.status !== 201 && r.status !== 200) return setError(detailOf(r.body));
    saveDraft(null);
    await useStore.getState().bootstrap().catch(() => {});
    // One toast: the toaster shows one at a time, and the warning is the part to read.
    const made = `Created ${r.body.bead_id || r.body.id.slice(0, 8)}. ${autostart ? "It is running." : "Created paused."}`;
    if (r.body.duplicate_warning) showToast(`${made} ${r.body.duplicate_warning}`, 6000);
    else showToast(made);
    navigate("/", { replace: true });
  };

  const attach = async (q: string) => {
    setError(null);
    if (looksLikePath(q)) {
      set({ [kind]: q.trim() });
      return sheet.close();
    }
    try {
      const r = await api.search({ q: q.trim(), repo: d.repo, kind, source_kind: "artifact", limit: 5 });
      if (!r.results.length) return setError(`No ${kind} found. Paste a repo-relative path instead.`);
      setHits(r.results);
      sheet.swap("attach-pick");
    } catch (e) {
      setError(e instanceof Error ? e.message : "The search failed.");
    }
  };

  const chip = (k: AttachKind) =>
    d[k] ? (
      <button key={k} type="button" className="ph-attach ph-is-set" onClick={() => { setKind(k); set({ [k]: "" }); }} aria-label={`Remove the ${k}`}>
        <span className="ph-attach-path">{k} · {d[k]}</span>
        <span aria-hidden="true">✕</span>
      </button>
    ) : (
      <button key={k} type="button" className="ph-attach" onClick={() => { setKind(k); setError(null); sheet.open("attach"); }}>+ {k}</button>
    );

  return (
    <>
      <header className="ph-composer-head">
        <button type="button" className="ph-back" onClick={() => (dirty ? sheet.open("discard") : back.go())}>Cancel</button>
        <h1 className="ph-composer-title">New work item</h1>
        <span className="ph-composer-pad" />
      </header>
      <div className="ph-content">
        <input className="ph-input ph-new-title" aria-label="Title" placeholder="Title" value={d.title} onChange={(e) => set({ title: e.target.value })} />
        <textarea className="ph-input ph-input-area" aria-label="Brief" placeholder="A line of brief. Every node reads it." value={d.brief} onChange={(e) => set({ brief: e.target.value })} />
        <label className="ph-field">
          <span>Repo</span>
          <select className="ph-input ph-select" value={d.repo} onChange={(e) => set({ repo: e.target.value, chain: repos.find((r) => r.path === e.target.value)?.default_chain_template ?? d.chain, spec: "", plan: "" })}>
            {repos.map((r) => <option key={r.path} value={r.path}>{repoName(r.path)}</option>)}
          </select>
        </label>
        <label className="ph-field">
          <span>Chain</span>
          <select className="ph-input ph-select" value={d.chain} onChange={(e) => set({ chain: e.target.value })}>
            {chains.filter((c) => !c.error).map((c) => <option key={c.id} value={c.id}>{`${c.id} · ${c.nodes.length} nodes · ${c.gates} gates${c.id === repo?.default_chain_template ? " (repo default)" : ""}`}</option>)}
          </select>
        </label>
        <div className="ph-attaches">{chip("spec")}{chip("plan")}</div>
        {error && !sheet.openId && <p className="ph-error" role="alert">{error}</p>}
      </div>
      <div className="ph-actionbar">
        <Button className="ph-btn" disabled={busy || !d.title.trim()} onClick={() => create(false)}>Create paused</Button>
        <Button className="ph-btn ph-btn-primary" variant="primary" disabled={busy || !d.title.trim()} onClick={() => create(true)}>Create and start</Button>
      </div>
      {sheet.is("discard") && (
        <ConfirmSheet title="Discard this draft?" text="The title and brief you typed will be lost." confirm={{ label: "Discard", danger: true, run: () => { saveDraft(null); sheet.goTo("/"); } }} onClose={sheet.close} />
      )}
      {sheet.is("attach") && (
        <EditSheet title={`Attach a ${kind}`} text={`Search this repo's ${kind}s, or paste a repo-relative path. The file must be inside the repo: it is committed on the item's branch.`} placeholder={`search ${kind}s, or paste a path`} submitLabel="Find" error={error} onSubmit={(q) => (q.trim() ? attach(q) : setError("Type a search or paste a path."))} onClose={sheet.close} />
      )}
      {sheet.is("attach-pick") && (
        <ChoiceSheet
          title={`Attach a ${kind}`}
          options={hits.map((h) => ({ value: h.path, label: h.title, hint: h.path }))}
          onPick={(p) => { set({ [kind]: p }); sheet.close(); }}
          onClose={sheet.close}
        />
      )}
    </>
  );
}
