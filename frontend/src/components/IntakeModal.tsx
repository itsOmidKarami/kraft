import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as api from "../api";

export function IntakeModal({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const [templates, setTemplates] = useState<string[]>(["quick-task"]);
  const [repo, setRepo] = useState("");
  const [title, setTitle] = useState("");
  const [tpl, setTpl] = useState("quick-task");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.getTemplates().then((ts) => setTemplates(ts.map((t) => t.id))).catch(() => {});
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
      <form className="modal" onSubmit={submit}>
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
