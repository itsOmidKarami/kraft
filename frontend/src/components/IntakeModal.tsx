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
import { SectionLabel, Switch } from "./ui";
import { backdropProps, useModal } from "../useModal";

/**
 * New work item (design 10, mobile m09). Two columns: the form on the left,
 * "OVERRIDES FOR THIS ITEM" + "Will happen on start" pinned to a 280px right
 * rail. The "Advanced · cross-repo" disclosure is collapsed by default and
 * only has anything in it when the repo actually has submodules — they come
 * from probing the repo's own .gitmodules, never from a list Kraft keeps of
 * its own.
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
  // Intake from existing artifacts (design §4): kind -> the path once
  // attached (the field becomes a chip), kind -> the search box's typed
  // text while it isn't, kind -> its hits.
  const [attachPath, setAttachPath] = useState<Record<string, string>>({});
  const [query, setQuery] = useState<Record<string, string>>({});
  const [hits, setHits] = useState<Record<string, SearchResult[]>>({});
  // Overrides: auto-escalate every gate (node_overrides), auto_gate
  // (Kraft-zr3s, arm agent review of those escalations), and a budget draft.
  const [autoEscalate, setAutoEscalate] = useState(false);
  const [autoGate, setAutoGate] = useState(true);
  const [budgetDraft, setBudgetDraft] = useState("");
  const [attemptsDraft, setAttemptsDraft] = useState("");
  const [wallClockDraft, setWallClockDraft] = useState("");
  const [skipped, setSkipped] = useState<Set<string>>(new Set());
  const ref = useModal<HTMLFormElement>(onClose);
  // Kraft-avvz: anything the user typed makes the backdrop click a no-op.
  const dirty = Boolean(title.trim() || description.trim() || repo);

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
  const autoEscalateOverrides: NodeOverrides = autoEscalate
    ? Object.fromEntries(
        selectedNodes
          .filter((n) => n.gate_after)
          .map((n) => [n.id, { auto_escalate: true }]),
      )
    : {};
  // Fix attempts/wall clock apply to every fix_loop node, not just gated ones
  // -- a node can end up with both an auto-escalate and a cap override, so
  // this merges per node rather than overwriting `autoEscalateOverrides`.
  const capOverrides: NodeOverrides =
    attemptsDraft.trim() || wallClockDraft.trim()
      ? Object.fromEntries(
          selectedNodes
            .filter((n) => n.fix_loop)
            .map((n) => [
              n.id,
              {
                ...(attemptsDraft.trim()
                  ? { attempts: Number(attemptsDraft) }
                  : {}),
                ...(wallClockDraft.trim()
                  ? { wall_clock_s: Number(wallClockDraft) * 60 }
                  : {}),
              },
            ]),
        )
      : {};
  const nodeOverrides: NodeOverrides = Object.fromEntries(
    [...new Set([...Object.keys(autoEscalateOverrides), ...Object.keys(capOverrides)])].map(
      (id) => [
        id,
        { ...autoEscalateOverrides[id], ...capOverrides[id] },
      ],
    ),
  );

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
    if (attemptsDraft.trim() && !Number.isFinite(Number(attemptsDraft))) {
      setError(`fix attempts must be a plain number, not "${attemptsDraft}"`);
      return;
    }
    if (wallClockDraft.trim() && !Number.isFinite(Number(wallClockDraft))) {
      setError(`wall clock must be a plain number, not "${wallClockDraft}"`);
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

  // Once attached (found via search or typed by hand and blurred), the
  // field becomes a chip -- one control does both jobs, not a search box
  // plus a separate path box.
  const attachOne = (kind: string, path: string) => {
    const trimmed = path.trim();
    if (!trimmed) return;
    setAttachPath((p) => ({ ...p, [kind]: trimmed }));
    setQuery((q) => ({ ...q, [kind]: "" }));
    setHits((h) => ({ ...h, [kind]: [] }));
  };

  // A hit selection or Enter is an explicit "attach this" — always trusted.
  // A blur is not: it fires on every click away from the field, including a
  // click on "Create and start", so raw search text ("auth") would otherwise
  // be attached as a path. Only treat it as one when it looks like one.
  const looksLikePath = (text: string) => /[/.]/.test(text);

  return (
    <div
      className="dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="New work item"
      {...backdropProps(onClose, dirty)}
    >
      <form
        className="dialog intake"
        onSubmit={(e) => submit(true, e)}
        ref={ref}
      >
        <div className="dialog-title">
          ⊕ New work item
          <span className="field-hint">
            {" "}
            · created paused — nothing spends tokens until you start it
          </span>
          <button
            type="button"
            className="dialog-close"
            aria-label="close"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </div>

        <div className="intake-grid">
          <div className="intake-main">
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
              <label htmlFor="intake-repo">Repo</label>
              <select
                id="intake-repo"
                className="input"
                aria-label="repo"
                value={repo}
                onChange={(e) => {
                  const path = e.target.value;
                  setRepo(path);
                  const r = allRepos.find((x) => x.path === path);
                  if (r?.default_chain_template) {
                    setTpl((cur) =>
                      templates.includes(r.default_chain_template!)
                        ? r.default_chain_template!
                        : cur,
                    );
                  }
                }}
              >
                <option value="" disabled>
                  select a repo…
                </option>
                {allRepos.map((r) => (
                  <option key={r.path} value={r.path} disabled={!r.enabled}>
                    {r.name}
                    {!r.enabled && " · disabled"}
                  </option>
                ))}
              </select>
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
              {KINDS.map(({ kind, label, gate }) => (
                <div key={kind} className="attachment-row">
                  {attachPath[kind] ? (
                    <>
                      <span className="chip attachment-chip">
                        {attachPath[kind]}
                        <button
                          type="button"
                          aria-label={`remove ${label}`}
                          onClick={() =>
                            setAttachPath((p) => {
                              const next = { ...p };
                              delete next[kind];
                              return next;
                            })
                          }
                        >
                          <X size={10} />
                        </button>
                      </span>
                      <p className="field-hint">
                        {label} attached → {gate} satisfied
                      </p>
                    </>
                  ) : (
                    <>
                      <input
                        className="input"
                        aria-label={label}
                        placeholder={`search ${label}s, or paste a repo-relative path`}
                        value={query[kind] ?? ""}
                        onChange={(e) =>
                          setQuery((q) => ({ ...q, [kind]: e.target.value }))
                        }
                        onKeyDown={(e) => {
                          if (e.key !== "Enter") return;
                          e.preventDefault();
                          const kindHits = hits[kind];
                          if (kindHits?.length) {
                            attachOne(kind, kindHits[0].path);
                          } else {
                            attachOne(kind, query[kind] ?? "");
                          }
                        }}
                        onBlur={() => {
                          const q = query[kind] ?? "";
                          if (looksLikePath(q)) attachOne(kind, q);
                        }}
                      />
                      {(hits[kind] ?? []).length > 0 && (
                        <div role="listbox" className="attachment-hits">
                          {(hits[kind] ?? []).map((h) => (
                            <button
                              type="button"
                              role="option"
                              aria-selected={false}
                              key={h.id}
                              className="attachment-hit"
                              // mousedown, not click: the field's onBlur fires
                              // first on a click and would attach the raw
                              // query text before this handler ever runs.
                              onMouseDown={(e) => {
                                e.preventDefault();
                                attachOne(kind, h.path);
                              }}
                            >
                              {h.title} <span className="field-hint">{h.path}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              ))}
            </div>

            <div className="field">
              <label>
                Chain template <span className="field-hint">· repo default</span>
              </label>
              <div className="chain-template-chips" role="radiogroup" aria-label="template">
                {templates.map((t) => {
                  const n = templateSummaries.find((s) => s.id === t)?.nodes.length;
                  return (
                    <button
                      key={t}
                      type="button"
                      className="chip"
                      role="radio"
                      aria-checked={tpl === t}
                      onClick={() => setTpl(t)}
                    >
                      {t}
                      {n != null && <span className="chain-pill-count"> {n} nodes</span>}
                    </button>
                  );
                })}
              </div>
            </div>

            {selectedNodes.length > 0 && (
              <div className="field chain-preview">
                <p className="field-hint">
                  {runCount} node{runCount === 1 ? "" : "s"} will run · click a
                  node to skip it · edit templates in Chains
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
          </div>

          <div className="intake-side">
            <SectionLabel>Overrides for this item</SectionLabel>
            <label className="control-row">
              <span>auto-escalate every gate</span>
              <Switch
                checked={autoEscalate}
                onChange={setAutoEscalate}
                label="auto-escalate every gate"
              />
            </label>
            <label className="control-row">
              <span>let an agent review those escalations first</span>
              <Switch
                checked={autoGate}
                onChange={setAutoGate}
                label="auto-review those escalations"
              />
            </label>
            <div className="control-row">
              <span>budget</span>
              <input
                className="input"
                inputMode="decimal"
                aria-label="budget"
                placeholder={`$${policy?.budget?.work_item_usd ?? "no cap"} (policy default)`}
                value={budgetDraft}
                onChange={(e) => setBudgetDraft(e.target.value)}
              />
            </div>
            <p className="field-hint">Blank uses the Policy default.</p>
            <div className="control-row">
              <span>fix attempts</span>
              <input
                className="input"
                inputMode="numeric"
                aria-label="fix attempts"
                placeholder={`${policy?.default.attempts ?? "—"} (policy default)`}
                value={attemptsDraft}
                onChange={(e) => setAttemptsDraft(e.target.value)}
              />
            </div>
            <div className="control-row">
              <span>wall clock (min)</span>
              <input
                className="input"
                inputMode="numeric"
                aria-label="wall clock, minutes"
                placeholder={
                  policy
                    ? `${Math.round(policy.default.wall_clock_s / 60)} (policy default)`
                    : "—"
                }
                value={wallClockDraft}
                onChange={(e) => setWallClockDraft(e.target.value)}
              />
            </div>
            <p className="field-hint">
              Blank uses the Policy default. Applies to every fix-loop node in this chain.
            </p>

            <SectionLabel>Will happen on start</SectionLabel>
            <p className="field-hint">
              {runCount} node{runCount === 1 ? "" : "s"} run
              {autoEscalate ? ", every gate escalates before you see it" : ""}
              {autoGate ? " and an agent reviews first" : ""}.
            </p>
          </div>
        </div>

        {error && <p className="form-error">{error}</p>}
        <div className="dialog-actions intake-foot">
          <button
            type="button"
            className="btn btn-secondary"
            disabled={busy}
            onClick={() => submit(false)}
          >
            ⊕ Create paused
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            ▷ Create and start
          </button>
          <span className="save-hint">created paused unless you start it</span>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
