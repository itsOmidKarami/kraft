import { useEffect, useState } from "react";
import { CaretDown, CaretRight, X } from "@phosphor-icons/react";
import { Link, useNavigate } from "react-router-dom";
import * as api from "../api";
import { useStore } from "../store";
import type { SearchResult, TemplateSummary } from "../types";
import { backdropProps, useModal } from "../useModal";

/**
 * New work item (design 1g). The "Advanced · cross-repo" disclosure is collapsed
 * by default and only has anything in it when the repo actually has submodules —
 * they come from probing the repo's own .gitmodules, never from a list Kraft
 * keeps of its own.
 */

const MERGE_POLICIES = [
  { id: "bump", label: "Bump" },
  { id: "skip", label: "Skip" },
  { id: "bump_no_mr", label: "Bump, no MR" },
];

// The gate each kind satisfies documents why picking one skips a chain phase;
// the server is the one that actually trims the chain.
const KINDS = [
  { kind: "spec" as const, docKind: "specs", gate: "spec_approval", label: "spec" },
  { kind: "plan" as const, docKind: "plans", gate: "plan_approval", label: "plan" },
];
export function IntakeModal({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  // Design 5.1 says this field offers the connected-repo set. Reading it off
  // existing work items instead left a fresh install with an empty field, no
  // suggestions and nothing saying a repo has to be connected first — the app's
  // primary action, dead on arrival. Connected repos lead; repos already in
  // flight follow, so an item outlives its repo being disconnected.
  const itemRepos = useStore((s) => Object.values(s.workItems).map((w) => w.repo));
  const [connected, setConnected] = useState<string[]>([]);
  const knownRepos = [...new Set([...connected, ...itemRepos])];
  const [templates, setTemplates] = useState<string[]>(["default"]);
  // GET /templates already returns each template's nodes (§8 chain preview
  // needs them); kept alongside the id list rather than re-fetched per pick.
  const [templateSummaries, setTemplateSummaries] = useState<TemplateSummary[]>([]);
  const [repo, setRepo] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tpl, setTpl] = useState("default");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [available, setAvailable] = useState<string[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [mergePolicy, setMergePolicy] = useState("bump");
  // Intake from existing artifacts (design task 7): kind -> the path that will
  // actually be attached, kind -> the search box's typed text, and kind -> its hits.
  const [attachPath, setAttachPath] = useState<Record<string, string>>({});
  const [query, setQuery] = useState<Record<string, string>>({});
  const [hits, setHits] = useState<Record<string, SearchResult[]>>({});
  const ref = useModal<HTMLFormElement>(onClose);

  useEffect(() => {
    api
      .getTemplates()
      .then((ts) => {
        const ids = ts.map((t) => t.id);
        if (!ids.length) return;
        setTemplates(ids);
        setTemplateSummaries(ts);
        // The optimistic "default" default is a guess made before this
        // answered. If the server does not offer it, the segmented control falls
        // back to its first option while state still says default — and we
        // would submit a chain the server never listed (Kraft-2ih).
        setTpl((cur) => (ids.includes(cur) ? cur : ids[0]));
      })
      .catch(() => {});
    api
      .getRepos()
      .then(({ repos }) => setConnected(repos.filter((r) => r.enabled).map((r) => r.path)))
      .catch(() => {});
  }, []);

  // Probing is read-only, so it can follow the repo field as it is typed.
  useEffect(() => {
    if (!repo.trim()) {
      setAvailable([]);
      setPicked([]);
      return;
    }
    const t = setTimeout(() => {
      api
        .probeRepo(repo)
        .then((p) => setAvailable(p.submodules))
        .catch(() => setAvailable([]));
    }, 300);
    return () => clearTimeout(t);
  }, [repo]);

  // Type-to-search per kind: GET /search requires a non-empty q, so this only
  // fires once a repo is entered and a character is typed for that kind.
  useEffect(() => {
    const t = setTimeout(() => {
      KINDS.forEach(({ kind, docKind }) => {
        const q = (query[kind] ?? "").trim();
        if (!repo.trim() || !q) {
          setHits((h) => ({ ...h, [kind]: [] }));
          return;
        }
        api
          .search({ q, repo, kind: docKind, source_kind: "artifact", limit: 5 })
          .then((r) => setHits((h) => ({ ...h, [kind]: r.results })))
          .catch(() => setHits((h) => ({ ...h, [kind]: [] })));
      });
    }, 300);
    return () => clearTimeout(t);
  }, [repo, query]);

  const attachments = KINDS.filter(({ kind }) => attachPath[kind]?.trim()).map(({ kind }) => ({
    kind,
    path: attachPath[kind].trim(),
  }));
  // §8: attaching a kind is the statement that its gate is satisfied, so the
  // node carrying that gate_after drops out of the chain — same rule as
  // `templates.materialize` on the server, applied here only to preview it.
  const satisfiedGates = new Set(
    KINDS.filter(({ kind }) => attachPath[kind]?.trim()).map(({ gate }) => gate),
  );
  const selectedNodes = templateSummaries.find((t) => t.id === tpl)?.nodes ?? [];

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { id } = await api.createWorkItem({
        repo,
        title,
        ...(description.trim() ? { description } : {}),
        ...(tpl === "default" ? {} : { chain_template: tpl }),
        ...(picked.length ? { submodules: picked, root_merge_policy: mergePolicy } : {}),
        ...(attachments.length ? { attachments } : {}),
      });
      onClose();
      nav(`/work-items/${id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="New work item" {...backdropProps(onClose)}>
      <form className="dialog intake" onSubmit={submit} ref={ref}>
        <div className="dialog-title">New work item</div>

        <div className="field">
          <label htmlFor="intake-repo">Repo</label>
          <input
            id="intake-repo"
            className="input"
            aria-label="repo"
            list="intake-repos"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            required
          />
          <datalist id="intake-repos">
            {knownRepos.map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
          {knownRepos.length === 0 && (
            <span className="field-hint">
              no repos connected yet — <Link to="/settings/repos">connect one in Settings</Link>,
              or type an absolute path
            </span>
          )}
        </div>

        <div className="field">
          <label htmlFor="intake-title">Title</label>
          <input
            id="intake-title"
            className="input"
            aria-label="title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
          />
        </div>

        <div className="field">
          <label htmlFor="intake-description">
            Description{" "}
            <span className="field-hint">· the brief the spec is written from</span>
          </label>
          <textarea
            id="intake-description"
            className="input"
            aria-label="description"
            rows={4}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        <div className="field">
          <label>
            Start from existing <span className="field-hint">· skips the phases these cover</span>
          </label>
          {KINDS.map(({ kind, label }) => (
            <div key={kind} className="attachment-row">
              <input
                className="input"
                aria-label={`existing ${label}`}
                placeholder={`search ${label}s in this repo`}
                value={query[kind] ?? ""}
                onChange={(e) => setQuery((q) => ({ ...q, [kind]: e.target.value }))}
              />
              <input
                className="input"
                aria-label={`${label} path`}
                placeholder="or a repo-relative path"
                value={attachPath[kind] ?? ""}
                onChange={(e) => setAttachPath((p) => ({ ...p, [kind]: e.target.value }))}
              />
              {(hits[kind] ?? []).map((h) => (
                <button
                  type="button"
                  key={h.id}
                  className="attachment-hit"
                  onClick={() => {
                    setAttachPath((p) => ({ ...p, [kind]: h.path }));
                    setQuery((q) => ({ ...q, [kind]: "" }));
                  }}
                >
                  {h.title} <span className="field-hint">{h.path}</span>
                </button>
              ))}
            </div>
          ))}
        </div>

        <div className="field">
          <label>
            Chain template <span className="field-hint">· repo default</span>
          </label>
          <div className="seg" role="radiogroup" aria-label="template">
            {templates.map((t) => (
              <label key={t} className="seg-opt">
                <input
                  type="radio"
                  name="intake-template"
                  value={t}
                  checked={tpl === t}
                  onChange={() => setTpl(t)}
                />
                {t}
              </label>
            ))}
          </div>
          {attachments.length > 0 && selectedNodes.length > 0 && (
            <p className="field-hint chain-preview">
              {selectedNodes.map((n, i) => {
                const struck = n.gate_after != null && satisfiedGates.has(n.gate_after);
                return (
                  <span key={n.id}>
                    {i > 0 && " → "}
                    {struck ? <s>{n.id}</s> : <span>{n.id}</span>}
                  </span>
                );
              })}
            </p>
          )}
        </div>

        {available.length > 0 && (
          <div className="disclosure">
            <button
              type="button"
              className="disclosure-head"
              aria-expanded={advanced}
              onClick={() => setAdvanced((v) => !v)}
            >
              {advanced ? <CaretDown size={12} /> : <CaretRight size={12} />}
              Advanced · cross-repo
            </button>
            {advanced && (
              <>
                <div className="field">
                  <label>
                    Submodules <span className="field-hint">· from .gitmodules</span>
                  </label>
                  <div className="submodules">
                    {available.map((path) => {
                      const on = picked.includes(path);
                      return (
                        <button
                          key={path}
                          type="button"
                          className={`tag ${on ? "tag-accent" : "tag-outline tag-off"}`}
                          aria-pressed={on}
                          onClick={() =>
                            setPicked(
                              on ? picked.filter((p) => p !== path) : [...picked, path],
                            )
                          }
                        >
                          {path}
                          {on && <X size={10} />}
                        </button>
                      );
                    })}
                  </div>
                </div>
                <div className="field">
                  <label>Root merge policy</label>
                  <div className="policy-radios">
                    {MERGE_POLICIES.map((p) => (
                      <label key={p.id} className="radio">
                        <input
                          type="radio"
                          name="root-merge-policy"
                          checked={mergePolicy === p.id}
                          onChange={() => setMergePolicy(p.id)}
                        />
                        <span className="dot" />
                        {p.label}
                      </label>
                    ))}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {error && <p className="form-error">{error}</p>}
        <div className="dialog-actions">
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Create
          </button>
        </div>
      </form>
    </div>
  );
}
