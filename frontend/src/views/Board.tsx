import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChainStrip } from "../components/ChainStrip";
import { IntakeModal } from "../components/IntakeModal";
import { useStore } from "../store";

const uniq = (xs: string[]) => [...new Set(xs)].sort();

export function Board() {
  const items = useStore((s) => Object.values(s.workItems));
  const [modal, setModal] = useState(false);
  const [repo, setRepo] = useState("");
  const [status, setStatus] = useState("");
  const [tpl, setTpl] = useState("");

  useEffect(() => {
    useStore.getState().bootstrap().catch(() => {});
  }, []);

  const shown = useMemo(
    () =>
      items.filter(
        (i) =>
          (!repo || i.repo === repo) &&
          (!status || i.status === status) &&
          (!tpl || i.chain_template === tpl),
      ),
    [items, repo, status, tpl],
  );

  return (
    <div className="board">
      <header className="board-toolbar">
        <select aria-label="repo" value={repo} onChange={(e) => setRepo(e.target.value)}>
          <option value="">all repos</option>
          {uniq(items.map((i) => i.repo)).map((r) => <option key={r}>{r}</option>)}
        </select>
        <select aria-label="status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">any status</option>
          {["active", "needs_human", "completed"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <select aria-label="template" value={tpl} onChange={(e) => setTpl(e.target.value)}>
          <option value="">any template</option>
          {uniq(items.map((i) => i.chain_template)).map((t) => <option key={t}>{t}</option>)}
        </select>
        <button onClick={() => setModal(true)}>New Work Item</button>
      </header>

      <ul className="board-list">
        {shown.map((i) => (
          <li key={i.id} data-testid="board-card" className="board-card">
            <Link to={`/work-items/${i.id}`}>
              <span className="repo-tag">{i.repo}</span>
              <span className="title">{i.title}</span>
              <span className="status" data-status={i.status}>{i.status}</span>
              <code className="wid">{i.id}</code>
            </Link>
            <ChainStrip item={i} size="sm" />
          </li>
        ))}
      </ul>

      {modal && <IntakeModal onClose={() => setModal(false)} />}
    </div>
  );
}
