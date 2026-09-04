import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as api from "../api";
import { useModal } from "../useModal";

export function IntakeModal({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const [templates, setTemplates] = useState<string[]>(["quick-task"]);
  const [repo, setRepo] = useState("");
  const [title, setTitle] = useState("");
  const [tpl, setTpl] = useState("quick-task");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ref = useModal<HTMLFormElement>(onClose);

  useEffect(() => {
    api
      .getTemplates()
      .then((ts) => {
        const ids = ts.map((t) => t.id);
        if (!ids.length) return;
        setTemplates(ids);
        // The optimistic "quick-task" default is a guess made before this
        // answered. If the server does not offer it, the select falls back to
        // rendering its first option while state still says quick-task — and we
        // would submit a chain the server never listed (Kraft-2ih).
        setTpl((cur) => (ids.includes(cur) ? cur : ids[0]));
      })
      .catch(() => {});
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = tpl === "quick-task" ? { repo, title } : { repo, title, chain_template: tpl };
      const { id } = await api.createWorkItem(body);
      onClose();
      nav(`/work-items/${id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="New work item">
      <form className="modal" onSubmit={submit} ref={ref}>
        <label>repo<input aria-label="repo" value={repo} onChange={(e) => setRepo(e.target.value)} required /></label>
        <label>title<input aria-label="title" value={title} onChange={(e) => setTitle(e.target.value)} required /></label>
        <label>template
          <select aria-label="template" value={tpl} onChange={(e) => setTpl(e.target.value)}>
            {templates.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        {error && <p className="form-error">{error}</p>}
        <div className="modal-actions">
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="submit" disabled={busy}>Create</button>
        </div>
      </form>
    </div>
  );
}
