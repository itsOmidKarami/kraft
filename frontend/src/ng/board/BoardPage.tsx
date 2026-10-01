import { useEffect, useMemo, useState } from "react";
import * as api from "../../api";
import { useStore } from "../../store";
import { FirstRun } from "../shell/FirstRun";
import { groupsOf } from "./model";
import { useBoardPrefs } from "./prefs";
import { useBoardQuery } from "./url";
import "./board.css";

/** `/ng`: the board (W6). First-run while no repo is connected, decided once
 *  on load so connecting one mid-setup does not swap the page away. */
export function BoardPage() {
  const prefs = useBoardPrefs();
  const [query] = useBoardQuery(prefs?.group_by);
  const [fresh, setFresh] = useState(false);
  const [allDone, setAllDone] = useState(false);
  const items = useStore((s) => s.workItems);

  useEffect(() => {
    api.getRepos().then((r) => setFresh(r.repos.length === 0)).catch(() => {});
  }, []);

  const groups = useMemo(
    () =>
      groupsOf(Object.values(items), {
        filter: { q: query.q, repo: query.repo, chain: query.chain },
        group: query.group,
        sort: query.sort,
        doneCap: allDone ? null : (prefs?.show_done ?? 5),
      }),
    [items, query.q, query.repo, query.chain, query.group, query.sort, allDone, prefs?.show_done],
  );

  if (fresh) return <FirstRun />;
  return (
    <div className="board-page">
      <h1 className="board-visually-hidden">Board</h1>
      <div className="board-list" aria-label="Work items">
        <div className="board-list-inner">
          {groups.map((g) => (
            <section key={g.key} className="board-group" aria-label={g.label}>
              <h2 className="board-group-head">
                <span>{g.label}</span>
                <span className="board-count">{g.total}</span>
              </h2>
              {g.rows.length === 0 && g.empty && <p className="board-empty">{g.empty}</p>}
              {g.rows.map((i) => (
                <div key={i.id} className="board-row">{i.title}</div>
              ))}
              {g.done && g.rows.length < g.total && (
                <button type="button" className="board-more" onClick={() => setAllDone(true)}>show all {g.total}</button>
              )}
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
