import { Link } from "react-router-dom";
import { NodeGlyph } from "../graph/NodeGlyph";
import { NodeIcon, TriangleAlert } from "../icons";
import { type HProblem, type Lane, type TaskRef, problemsOfTask, taskLeaf, taskName } from "./model";

const nodeOf = (path: string) => path.split(".")[0];
export const chainLink = (t: TaskRef) => `/templates/chains/${encodeURIComponent(t.chain)}/nodes/${encodeURIComponent(nodeOf(t.path))}`;

function Glyph({ t, problems }: { t: TaskRef; problems: HProblem[] }) {
  const bad = problemsOfTask(problems, t).length > 0;
  const name = `${taskLeaf(t)}${t.fallback ? " (fallback)" : ""}`;
  return (
    <Link
      className={`hn-glyph${bad ? " is-bad" : ""}`}
      to={chainLink(t)}
      aria-label={`${taskName(t)}${t.fallback ? ", fallback" : ""}${bad ? ", has a problem" : ""}. Open in Chains`}
      title={`${taskName(t)}${bad ? "\n" + problemsOfTask(problems, t)[0].message : ""}`}
      onClick={(e) => e.stopPropagation()}
    >
      <NodeGlyph kind="exec" size="md" taskKind="agent" prob={bad} />
      <span className="hn-glyph-name">{name}</span>
    </Link>
  );
}

/** The canvas's lanes (Decisions §11): a fixed 520px each; the canvas scrolls. */
export function Lanes({ lanes, pill, selected, empty, problems, onOpen }: {
  lanes: Lane[];
  pill: string;
  /** The open lane's `open` key, marked. */
  selected?: string | null;
  empty: string;
  problems: HProblem[];
  onOpen: (key: string) => void;
}) {
  return (
    <>
      <span className="hn-pill">{pill}</span>
      {lanes.length === 0 && <p className="hn-empty">{empty}</p>}
      {lanes.map((l) => (
        <section
          key={l.key}
          className={`hn-lane${l.red ? " is-red" : ""}${l.dash ? " is-dash" : ""}${l.dim ? " is-dim" : ""}${selected && selected === l.open ? " is-sel" : ""}`}
          aria-label={l.title}
          onClick={l.open ? (e) => { e.stopPropagation(); onOpen(l.open!); } : (e) => e.stopPropagation()}
        >
          {l.open ? (
            <button type="button" className="hn-lane-head" onClick={(e) => { e.stopPropagation(); onOpen(l.open!); }} aria-label={`${l.title}${l.tag ? `, ${l.tag}` : ""}${l.red ? ", has a problem" : ""}`}>
              <LaneHead l={l} />
            </button>
          ) : (
            <div className="hn-lane-head"><LaneHead l={l} /></div>
          )}
          {l.tasks.length > 0 && (
            <div className="hn-glyphs">
              {l.tasks.map((t) => <Glyph key={`${t.chain}/${t.path}${t.fallback ? "/f" : ""}`} t={t} problems={problems} />)}
              {l.more > 0 && <span className="hn-more">+{l.more} more</span>}
            </div>
          )}
          {l.note && <p className={`hn-lane-note${l.red ? " is-bad" : ""}`}>{l.note}</p>}
        </section>
      ))}
    </>
  );
}

function LaneHead({ l }: { l: Lane }) {
  return (
    <>
      <span className="hn-lane-icon">{l.icon === "triangle-alert" ? <TriangleAlert size={14} aria-hidden /> : <NodeIcon name={l.icon} size={14} />}</span>
      <span className="hn-lane-title">{l.title}</span>
      {l.model && <><span className="hn-lane-arrow" aria-hidden>→</span><span className="hn-lane-model">{l.model}</span></>}
      {l.effort && <span className="hn-lane-chip">{l.effort}</span>}
      {l.tag && <span className="hn-lane-tag" data-access={l.tag.toLowerCase()}>{l.tag}</span>}
      <span className="hn-lane-gap" />
      <span className="hn-lane-right">{l.right}</span>
    </>
  );
}
