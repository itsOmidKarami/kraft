import { Link } from "react-router-dom";
import type { ReactNode } from "react";
import type { Row } from "./rows";
import { refUrl, SECTIONS, SECTION_LABEL } from "./types";

/** A list of components, each a link to its own page, or the word for none. */
function Links({ rows, none }: { rows: Row[]; none: string }) {
  if (!rows.length) return <p className="lib-landing-none">{none}</p>;
  return <ul>{rows.map((x) => <li key={x.id}><Link className="lib-landing-link" to={refUrl(x.id)}>{x.id}</Link></li>)}</ul>;
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return <section><h2>{title}</h2>{children}</section>;
}

/** What the Library shows with nothing selected: how much it holds per section, and the components that need a look. */
export function LibraryLanding({ rows, loaded }: { rows: Row[]; loaded: boolean }) {
  if (!rows.length) return <div className="lib-landing"><p>The library is empty. + New adds the first component.</p></div>;
  return (
    <div className="lib-landing">
      <h1>Library</h1>
      <ul className="lib-landing-counts" aria-label="Components per section">
        {SECTIONS.map((s) => {
          const inSection = rows.filter((x) => x.section === s);
          const body = <><b>{inSection.length}</b>{SECTION_LABEL[s].toLowerCase()}</>;
          return <li key={s}>{inSection.length ? <Link to={`/templates/library?section=${s}`}>{body}</Link> : <span>{body}</span>}</li>;
        })}
      </ul>
      <Block title="Problems"><Links rows={rows.filter((x) => x.problem)} none="None." /></Block>
      <Block title="Used by no chain">{loaded ? <Links rows={rows.filter((x) => x.used === "unused")} none="None." /> : <p className="lib-landing-none">Reading…</p>}</Block>
      <Block title="Changed in the draft"><Links rows={rows.filter((x) => x.mark)} none="Nothing yet." /></Block>
    </div>
  );
}
