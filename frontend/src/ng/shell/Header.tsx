import { Fragment } from "react";
import { Link, useLocation } from "react-router-dom";
import { useStore } from "../../store";
import { crumbsFor, type Crumb } from "./crumbs";
import { usePageItem } from "./pageItem";

export function Crumbs({ crumbs }: { crumbs: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="ng-crumbs-nav">
      <ol className="ng-crumbs">
        {crumbs.map((c, i) => {
          if (c.kind === "ext")
            return (
              <li key={i} className="ng-crumb ng-crumb-ext">
                <a href={c.href} target="_blank" rel="noopener noreferrer" title={c.title}>{c.text}</a>
              </li>
            );
          const last = i === crumbs.length - 1 || crumbs[i + 1].kind === "ext";
          const cut = c.kind !== "mid";
          const cls = `ng-crumb ng-crumb-${c.kind}`;
          const cutProps = cut ? { "data-allow-ellipsis": "", title: c.title ?? c.text } : {};
          return (
            <Fragment key={i}>
              {!last && (c.to || c.href) ? (
                <li className={cls} {...cutProps}>
                  {c.to ? <Link to={c.to}>{c.text}</Link> : <a href={c.href}>{c.text}</a>}
                </li>
              ) : (
                <li className={cls} aria-current={last ? "page" : undefined} {...cutProps}>{c.text}</li>
              )}
            </Fragment>
          );
        })}
      </ol>
    </nav>
  );
}

/** One row above the page: where you are, then the page's own actions. */
export function Header({ actionsRef }: { actionsRef: (el: HTMLDivElement | null) => void }) {
  const { pathname } = useLocation();
  const items = useStore((s) => s.workItems);
  const page = usePageItem((s) => s.item);
  const crumbs = crumbsFor(pathname, (id) => (page?.id === id ? page : items[id]));
  return (
    <header className="ng-header">
      <Crumbs crumbs={crumbs} />
      <div className="ng-header-actions" ref={actionsRef} />
    </header>
  );
}
