import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { detailOf, request } from "../../http";
import { useConfigDraft, type ConfigDraft } from "../../templates/draft/useConfigDraft";
import { problemText } from "../../templates/problems";
import { FIELDS, FORGES, patchFor, sourceOf, valueOf, type RepoField } from "../../templates/repos/fields";
import { setRepo } from "../../templates/repos/RepoConfig";
import { problemsOf, repoName, reposOf, runningOf, type RepoView } from "../../templates/repos/types";
import { showToast } from "../../ui/Toast";
import { ConfirmSheet, useSheet } from "../nav/Sheet";
import { AreaScreen } from "./AreaScreen";
import { Group, useEditor, type RowSpec } from "./kit";

const url = (r: RepoView) => `/templates/repos/${encodeURIComponent(r.name || r.path)}`;

/** `/templates/repos`: Connected, Detected, and a filter (W17 brief M.2). Connecting by typing a path is the desktop's. */
export function ReposList() {
  const draft = useConfigDraft("repos", "repos");
  const [params, setParams] = useSearchParams();
  const q = (params.get("q") ?? "").trim().toLowerCase();
  const data = draft.view ? reposOf(draft.view.result) : null;
  const running = draft.view ? runningOf(draft.view.result) : {};
  const [error, setError] = useState<string | null>(null);
  const connect = async (r: RepoView) => {
    setError(null);
    const a = await draft.ops([{ op: "connect_detected", path: r.path }], { quiet: true });
    if (a.status !== 200) return setError(detailOf(a.body));
    showToast(`Connected ${repoName(r)} in the draft.`);
  };
  const shown = (data?.repos ?? []).filter((r) => !q || `${repoName(r)} ${r.path}`.toLowerCase().includes(q));
  return (
    <AreaScreen title="Repos" sub="Repositories Kraft works in, and the defaults each one carries." draft={draft}>
      <label className="ph-search-field ph-area-search">
        <input type="search" className="ph-search-input" aria-label="Filter repos" placeholder="Filter repos" value={params.get("q") ?? ""} onChange={(e) => { const n = new URLSearchParams(params); if (e.target.value) n.set("q", e.target.value); else n.delete("q"); setParams(n, { replace: true }); }} />
      </label>
      {draft.status === "error" && <p className="ph-error" role="alert">The repos could not be read.</p>}
      {error && <p className="ph-error" role="alert">{error}</p>}
      {data && data.repos.length === 0 && <p className="ph-empty">No repository is connected. Run <code>kraft repo connect</code> in a repo on the machine, or connect one from a computer.</p>}
      {data && data.repos.length > 0 && (
        <Group
          title="Connected"
          rows={shown.map((r): RowSpec => ({
            key: r.path, label: repoName(r), mono: true, to: url(r),
            sub: `${r.path} · chain ${String(r.entry.default_chain_template ?? "default")}${r.entry.test_command ? ` · ${String(r.entry.test_command)}` : ""}`,
            chips: [{ label: r.entry.enabled === false ? "off" : "on", tone: r.entry.enabled === false ? undefined : "ok" }, ...(running[r.path] ? [{ label: `${running[r.path]} open` }] : []), ...(problemsOf(draft.view!.result, r.path).length ? [{ label: "problem", tone: "bad" as const }] : [])],
          }))}
        />
      )}
      {data && data.detected.length > 0 && (
        <Group title="Detected" note="Workspace members Kraft found that are not connected." rows={data.detected.map((r): RowSpec => ({ key: r.path, label: repoName(r), mono: true, sub: r.path, chips: [{ label: "connect", tone: "ok" }], onClick: () => void connect(r) }))} />
      )}
    </AreaScreen>
  );
}

const sendTo = (draft: ConfigDraft, repo: RepoView, f: RepoField) => async (t: string) => {
  const p = f.parse(t);
  if ("error" in p) return p.error;
  return setRepo(draft, repo, patchFor(repo, f, p.value));
};

/** `/templates/repos/:repo`: every field the desktop's Config tab edits, each with where its value comes from, and Disconnect. */
export function RepoView() {
  const { repo: param } = useParams();
  const draft = useConfigDraft("repos", "repos");
  const { edit, node } = useEditor();
  const sheet = useSheet();
  const [chains, setChains] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void request<{ id: string }[]>("/templates/chains").then((a) => a.status === 200 && Array.isArray(a.body) && setChains(a.body.map((c) => c.id)));
  }, []);
  const data = draft.view ? reposOf(draft.view.result) : null;
  const repo = data?.repos.find((x) => x.path === param || x.name === param);
  const running = repo && draft.view ? (runningOf(draft.view.result)[repo.path] ?? 0) : 0;
  const own = repo && draft.view ? problemsOf(draft.view.result, repo.path) : [];
  const changed = (key: string) => !!draft.view?.result.changes.find((c) => c.path === repo?.path)?.fields?.includes(key);

  const rowOf = (f: RepoField): RowSpec => {
    const v = valueOf(repo!, f);
    const shown = f.show(f.key === "steering" ? repo!.resolved.steering : v) || f.fallback || "not set";
    const base: RowSpec = { key: f.key, label: f.label, value: shown, sub: sourceOf(repo!, f), changed: changed(f.key.replace(/^policy\./, "policy")) };
    if (f.choice) {
      const options = (f.choice === "chain" ? chains : FORGES).map((x) => ({ value: x, label: x }));
      return { ...base, onEdit: () => edit({ kind: "choice", title: f.label, value: typeof v === "string" ? v : null, options, set: (x) => sendTo(draft, repo!, f)(x) }) };
    }
    return { ...base, mono: f.key.includes("command"), onEdit: () => edit({ kind: "text", title: f.label, help: "Leave empty to clear it, which falls back to the default.", value: f.show(f.key === "steering" ? repo!.resolved.steering : v), placeholder: f.placeholder, set: sendTo(draft, repo!, f) }) };
  };

  const disconnect = async () => {
    if (!repo) return;
    setError(null);
    const a = await draft.ops([{ op: "remove_repo", path: repo.path }], { quiet: true });
    if (a.status !== 200) return setError(detailOf(a.body));
    sheet.goTo("/templates/repos");
    showToast(`${repoName(repo)} is disconnected in the draft. Publish to apply it.`);
  };

  return (
    <AreaScreen title={repo ? repoName(repo) : "Repo"} sub={repo?.path} draft={draft}>
      {data && !repo && <p className="ph-empty">There is no connected repo {param}.</p>}
      {own.length > 0 && <Group title="Problems" rows={own.map((p, i): RowSpec => ({ key: `p${i}`, label: problemText(p), sub: p.field ?? undefined, chips: [{ label: "problem", tone: "bad" }] }))} />}
      {repo && (
        <>
          <Group
            rows={[
              { label: "enabled", sw: repo.entry.enabled !== false, changed: changed("enabled"), onSwitch: (on) => void setRepo(draft, repo, { enabled: on }).then((e) => e && showToast(e)) },
              ...(running ? [{ label: "open items", value: String(running) } as RowSpec] : []),
              ...FIELDS.map(rowOf),
            ]}
          />
          <Group rows={[{ label: "Disconnect repo", danger: true, onClick: () => { setError(null); sheet.open("disconnect"); } }]} />
        </>
      )}
      {node}
      {sheet.is("disconnect") && repo && (
        <ConfirmSheet
          title={`Disconnect ${repoName(repo)}?`}
          text={running > 0 ? `${repoName(repo)} has ${running} open ${running === 1 ? "item" : "items"}. Disconnect is refused until ${running === 1 ? "it finishes or is cancelled" : "they finish or are cancelled"}.` : "Kraft stops working in it once you publish. Its items and branches are left as they are."}
          error={error}
          confirm={{ label: "Disconnect", danger: true, disabled: running > 0, run: () => void disconnect() }}
          onClose={sheet.close}
        />
      )}
    </AreaScreen>
  );
}
