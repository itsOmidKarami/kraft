import { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import { request, detailOf } from "../http";
import { PauseText } from "../templates/panes/controls";
import type { ConfigDraft } from "../templates/draft/useConfigDraft";
import { authoredAt } from "../templates/draft/view";
import type { Row } from "./rows";
import type { Use } from "./types";

const str = (v: unknown) => (typeof v === "string" ? v : "");

/** A steering profile's Instructions tab: one text, saved on a pause (W10's 800 ms), empty is a problem. */
export function Instructions({ draft, path }: { draft: ConfigDraft; path: string }) {
  const text = str(authoredAt(draft.view!.result, draft.scope, path)?.instructions);
  return (
    <PauseText
      label="instructions"
      long
      rows={12}
      value={text}
      required
      bad={!text.trim()}
      sub={!text.trim() ? "Required." : "Added to what an agent task reads at launch, after the repository's own steering."}
      onText={(t) => draft.field(path, "instructions", t, true)}
      onBlur={draft.flush}
    />
  );
}

interface Section {
  kind: string;
  source: string;
  text: string;
}
type Preview = { state: "loading" } | { state: "none"; why: string } | { state: "error"; detail: string } | { state: "ok"; sections: Section[] };

/** The repositories to preview in, once per page. */
function useRepoNames(): string[] | null {
  const [names, setNames] = useState<string[] | null>(null);
  useEffect(() => {
    let live = true;
    request<{ repos: { name: string }[] }>("/repos").then((a) => live && setNames(a.status === 200 ? a.body.repos.map((x) => x.name) : []));
    return () => {
      live = false;
    };
  }, []);
  return names;
}

/** What an agent task reads at launch, in launch order (Decisions §10 Steering): for one use of the profile
 *  in a chain and one repository, every section the server builds, each expanding to its text. It reads the
 *  published templates, so a draft change to the profile is not in it, and the pane says so. */
export function SteeringCanvas({ row, uses, reserve }: { row: Row; uses: Use[] | null; reserve: number }) {
  const repos = useRepoNames();
  const [useAt, setUseAt] = useState(0);
  const [repoAt, setRepoAt] = useState(0);
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [preview, setPreview] = useState<Preview>({ state: "loading" });
  const use = uses?.[useAt];
  const repo = repos?.[repoAt];

  useEffect(() => {
    setUseAt(0);
    setOpen(new Set());
  }, [row.id]);

  useEffect(() => {
    if (uses === null || repos === null) return setPreview({ state: "loading" });
    if (!use) return setPreview({ state: "none", why: "Use this profile on a task to preview it." });
    if (!repo) return setPreview({ state: "none", why: "Connect a repository to preview." });
    let live = true;
    setPreview({ state: "loading" });
    request<{ sections: Section[] }>(`/templates/steering/preview?chain=${encodeURIComponent(use.chain)}&task=${encodeURIComponent(use.path)}&repo=${encodeURIComponent(repo)}`).then((a) => {
      if (!live) return;
      setPreview(a.status === 200 ? { state: "ok", sections: a.body.sections } : { state: "error", detail: detailOf(a.body) });
      setOpen(new Set());
    });
    return () => {
      live = false;
    };
  }, [use?.chain, use?.path, repo, uses === null, repos === null]);

  const toggle = (i: number) => setOpen((o) => {
    const next = new Set(o);
    if (!next.delete(i)) next.add(i);
    return next;
  });
  const mine = (s: Section) => s.kind === "steering" && s.source.endsWith(`:${row.name}`);

  return (
    <div className="lib-preview" style={{ right: reserve }}>
      <h2 className="lib-preview-title">Launch order</h2>
      <div className="lib-preview-pick">
        <label>
          <span>as used in</span>
          <select aria-label="Use" value={useAt} disabled={!uses?.length} onChange={(e) => setUseAt(Number(e.target.value))}>
            {(uses ?? []).map((u, i) => <option key={`${u.chain}|${u.path}`} value={i}>{u.chain} · {u.path}</option>)}
          </select>
        </label>
        <label>
          <span>in</span>
          <select aria-label="Repository" value={repoAt} disabled={!repos?.length} onChange={(e) => setRepoAt(Number(e.target.value))}>
            {(repos ?? []).map((n, i) => <option key={n} value={i}>{n}</option>)}
          </select>
        </label>
      </div>
      {row.mark && <p className="lib-preview-note">Preview shows the published text. Publish to see this change.</p>}
      {preview.state === "loading" && <p className="lib-preview-note">Loading…</p>}
      {preview.state === "none" && <p className="lib-preview-note">{preview.why}</p>}
      {preview.state === "error" && <p className="lib-preview-note is-bad" role="alert">{preview.detail}</p>}
      {preview.state === "ok" && (
        <ol className="lib-sections">
          {preview.sections.map((s, i) => (
            <li key={i} className={`lib-section${mine(s) ? " is-mine" : ""}`}>
              <button type="button" className="lib-section-row" aria-expanded={open.has(i)} onClick={() => toggle(i)}>
                <ChevronRight size={12} aria-hidden className="lib-section-chev" />
                <span className="lib-section-kind">{s.kind}</span>
                <span className="lib-section-source">{s.source}</span>
                {mine(s) && <span className="lib-chip">this profile</span>}
              </button>
              {open.has(i) && <pre className="lib-section-text">{s.text}</pre>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
