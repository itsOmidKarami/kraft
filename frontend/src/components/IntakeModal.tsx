import { useEffect, useState } from "react";
import { CaretDown, CaretRight, Flag, X } from "@phosphor-icons/react";
import { Link, useNavigate } from "react-router-dom";
import * as api from "../api";
import type {
  NodeOverrides,
  Policy,
  Repo,
  SearchResult,
  TemplateSummary,
} from "../types";
import { Switch } from "./ui";
import { backdropProps, useModal } from "../useModal";

/**
 * New work item (UI v2 · 10, mobile m09). The "Advanced · cross-repo"
 * disclosure is collapsed by default and only has anything in it when the
 * repo actually has submodules — they come from probing the repo's own
 * .gitmodules, never from a list Kraft keeps of its own.
 */

const MERGE_POLICIES = [
  { id: "bump", label: "Bump" },
  { id: "skip", label: "Skip" },
  { id: "bump_no_mr", label: "Bump, no MR" },
];

// The gate each kind satisfies documents why picking one skips a chain phase;
// the server is the one that actually trims the chain.
const KINDS = [
  {
    kind: "spec" as const,
    docKind: "specs",
    gate: "spec_approval",
    label: "spec",
  },
  {
    kind: "plan" as const,
    docKind: "plans",
    gate: "plan_approval",
    label: "plan",
  },
];

export function IntakeModal({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const [allRepos, setAllRepos] = useState<Repo[]>([]);
  const [templates, setTemplates] = useState<string[]>(["default"]);
  // GET /templates already returns each template's nodes (§8 chain preview
  // needs them); kept alongside the id list rather than re-fetched per pick.
  const [templateSummaries, setTemplateSummaries] = useState<TemplateSummary[]>(
    [],
  );
  const [policy, setPolicy] = useState<Policy | null>(null);
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
  // 06 Overrides: auto-escalate every gate (node_overrides), auto_gate
  // (Kraft-zr3s, arm agent review of those escalations), and a budget draft.
  const [autoEscalate, setAutoEscalate] = useState(false);
  const [autoGate, setAutoGate] = useState(true);
  const [budgetDraft, setBudgetDraft] = useState("");
  const [skipped, setSkipped] = useState<Set<string>>(new Set());
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
      .then(({ repos }) => setAllRepos(repos))
      .catch(() => {});
    api
      .getPolicy()
      .then(setPolicy)
      .catch(() => {});
  }, []);

  // Reset the skip set whenever the template changes -- a node id from one
  // template's chain is meaningless against another's.
  useEffect(() => {
    setSkipped(new Set());
  }, [tpl]);

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

  const attachments = KINDS.filter(({ kind }) => attachPath[kind]?.trim()).map(
    ({ kind }) => ({
      kind,
      path: attachPath[kind].trim(),
    }),
  );
  // §8: attaching a kind is the statement that its gate is satisfied, so the
  // node carrying that gate_after drops out of the chain — same rule as
  // `templates.materialize` on the server, applied here only to preview it.
  const satisfiedGates = new Set(
    KINDS.filter(({ kind }) => attachPath[kind]?.trim()).map(
      ({ gate }) => gate,
    ),
  );
  const selectedNodes =
    templateSummaries.find((t) => t.id === tpl)?.nodes ?? [];
  const nodeOverrides: NodeOverrides = autoEscalate
    ? Object.fromEntries(
        selectedNodes
          .filter((n) => n.gate_after)
          .map((n) => [n.id, { auto_escalate: true }]),
      )
    : {};

  const submit = async (autostart: boolean, e?: React.FormEvent) => {
    e?.preventDefault();
    // `Number("$20")` and `Number("20 usd")` are both NaN, and NaN through
    // JSON.stringify becomes `null` -- the server reads an explicit `null`
    // as "no cap", so a typo here would silently drop the spend cap instead
    // of failing loud.
    const budgetUsd = budgetDraft.trim() ? Number(budgetDraft) : undefined;
    if (budgetUsd !== undefined && !Number.isFinite(budgetUsd)) {
      setError(`budget must be a plain number, not "${budgetDraft}"`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { id } = await api.createWorkItem({
        repo,
        title,
        ...(description.trim() ? { description } : {}),
        ...(tpl === "default" ? {} : { chain_template: tpl }),
        ...(picked.length
          ? { submodules: picked, root_merge_policy: mergePolicy }
          : {}),
        ...(attachments.length ? { attachments } : {}),
        skip_nodes: [...skipped],
        ...(budgetUsd !== undefined ? { budget_usd: budgetUsd } : {}),
        ...(Object.keys(nodeOverrides).length
          ? { node_overrides: nodeOverrides }
          : {}),
        auto_gate: autoGate,
        autostart,
      });
      onClose();
      nav(`/work-items/${id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  const runCount = selectedNodes.length - skipped.size - satisfiedGates.size;

  return (
    <div
      className="dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="New work item"
      {...backdropProps(onClose)}
    >
      <form
        className="dialog intake"
        onSubmit={(e) => submit(true, e)}
        ref={ref}
      >
        <div className="dialog-title">New work item</div>

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
            <span className="field-hint">
              · becomes the brief every node reads
            </span>
          </label>
          <textarea
            id="intake-description"
            className="input"
            aria-label="description"
            rows={4}
            placeholder="Context, constraints, what done looks like"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        <div className="field">
          <label>Repo</label>
          <div className="repo-chips" role="radiogroup" aria-label="repo">
            {allRepos.map((r) => (
              <button
                key={r.path}
                type="button"
                className="chip"
                aria-pressed={repo === r.path}
                disabled={!r.enabled}
                onClick={() => {
                  setRepo(r.path);
                  if (r.default_chain_template) {
                    setTpl((cur) =>
                      templates.includes(r.default_chain_template!)
                        ? r.default_chain_template!
                        : cur,
                    );
                  }
                }}
              >
                {r.name}
                {!r.enabled && " · disabled"}
              </button>
            ))}
          </div>
          {allRepos.length === 0 && (
            <span className="field-hint">
              no repos connected yet —{" "}
              <Link to="/settings/repos">connect one in Settings</Link>
            </span>
          )}
        </div>

        <div className="field">
          <label>
            Start from existing{" "}
            <span className="field-hint">· skips the phase it covers</span>
          </label>
          {KINDS.map(({ kind, label }) => (
            <div key={kind} className="attachment-row">
              <input
                className="input"
                aria-label={`existing ${label}`}
                placeholder={`search ${label}s in this repo`}
                value={query[kind] ?? ""}
                onChange={(e) =>
                  setQuery((q) => ({ ...q, [kind]: e.target.value }))
                }
              />
              <input
                className="input"
                aria-label={`${label} path`}
                placeholder="or a repo-relative path"
                value={attachPath[kind] ?? ""}
                onChange={(e) =>
                  setAttachPath((p) => ({ ...p, [kind]: e.target.value }))
                }
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
            {templates.map((t) => {
              const n = templateSummaries.find((s) => s.id === t)?.nodes.length;
              return (
                <label key={t} className="seg-opt">
                  <input
                    type="radio"
                    name="intake-template"
                    value={t}
                    checked={tpl === t}
                    onChange={() => setTpl(t)}
                  />
                  {t}
                  {n != null && ` · ${n}`}
                </label>
              );
            })}
          </div>
        </div>

        {selectedNodes.length > 0 && (
          <div className="field chain-preview">
            <p className="field-hint">
              {runCount} node{runCount === 1 ? "" : "s"} will run · tap a node
              to skip it
            </p>
            <p className="field-hint">
              {selectedNodes.map((n, i) => {
                const autoSkipped =
                  n.gate_after != null && satisfiedGates.has(n.gate_after);
                const struck = autoSkipped || skipped.has(n.id);
                return (
                  <span key={n.id}>
                    {i > 0 && " → "}
                    <button
                      type="button"
                      className="chain-preview-node"
                      disabled={autoSkipped}
                      onClick={() =>
                        setSkipped((s) => {
                          const next = new Set(s);
                          if (next.has(n.id)) next.delete(n.id);
                          else next.add(n.id);
                          return next;
                        })
                      }
                    >
                      {struck ? <s>{n.id}</s> : n.id}
                      {n.gate_after && <Flag size={9} weight="fill" />}
                    </button>
                  </span>
                );
              })}
            </p>
          </div>
        )}

        <div className="field">
          <label>Overrides</label>
          <label className="control-row">
            <Switch
              checked={autoEscalate}
              onChange={setAutoEscalate}
              label="auto-escalate every gate"
            />
            auto-escalate every gate
          </label>
          <label className="control-row">
            <Switch
              checked={autoGate}
              onChange={setAutoGate}
              label="auto-review those escalations"
            />
            auto_gate — let an agent review before a human sees it
          </label>
          <input
            className="input"
            inputMode="decimal"
            aria-label="budget"
            placeholder={`$ ${policy?.budget?.work_item_usd ?? "no cap"} (policy default)`}
            value={budgetDraft}
            onChange={(e) => setBudgetDraft(e.target.value)}
          />
        </div>

        <p className="field-hint">
          Will happen on start: {runCount} node{runCount === 1 ? "" : "s"} run
          {autoEscalate ? ", every gate escalates before you see it" : ""}
          {autoGate ? " and an agent reviews first" : ""}.
        </p>

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
                    Submodules{" "}
                    <span className="field-hint">· from .gitmodules</span>
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
                              on
                                ? picked.filter((p) => p !== path)
                                : [...picked, path],
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
          <button
            type="button"
            className="btn btn-secondary"
            disabled={busy}
            onClick={() => submit(false)}
          >
            + Create paused
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            ▷ Create and start
          </button>
        </div>
      </form>
    </div>
  );
}
