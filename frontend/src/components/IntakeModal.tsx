import { useEffect, useState } from "react";
import { CaretDown, CaretRight, X } from "@phosphor-icons/react";
import { useNavigate } from "react-router-dom";
import * as api from "../api";
import { useStore } from "../store";
import { useModal } from "../useModal";

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
export function IntakeModal({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const knownRepos = useStore((s) => [
    ...new Set(Object.values(s.workItems).map((w) => w.repo)),
  ]);
  const [templates, setTemplates] = useState<string[]>(["quick-task"]);
  const [repo, setRepo] = useState("");
  const [title, setTitle] = useState("");
  const [tpl, setTpl] = useState("quick-task");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [available, setAvailable] = useState<string[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [mergePolicy, setMergePolicy] = useState("bump");
  const ref = useModal<HTMLFormElement>(onClose);

  useEffect(() => {
    api
      .getTemplates()
      .then((ts) => {
        const ids = ts.map((t) => t.id);
        if (!ids.length) return;
        setTemplates(ids);
        // The optimistic "quick-task" default is a guess made before this
        // answered. If the server does not offer it, the segmented control falls
        // back to its first option while state still says quick-task — and we
        // would submit a chain the server never listed (Kraft-2ih).
        setTpl((cur) => (ids.includes(cur) ? cur : ids[0]));
      })
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

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { id } = await api.createWorkItem({
        repo,
        title,
        ...(tpl === "quick-task" ? {} : { chain_template: tpl }),
        ...(picked.length ? { submodules: picked, root_merge_policy: mergePolicy } : {}),
      });
      onClose();
      nav(`/work-items/${id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="New work item">
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
