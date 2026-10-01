import { Fragment } from "react";
import { Link, useLocation } from "react-router-dom";
import { useStore } from "../../store";
import { crumbsFor, type Crumb } from "./crumbs";

export function Crumbs({ crumbs }: { crumbs: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="ng-crumbs-nav">
      <ol className="ng-crumbs">
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1;
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
  const crumbs = crumbsFor(pathname, (id) => items[id]);
  return (
    <header className="ng-header">
      <Crumbs crumbs={crumbs} />
      <div className="ng-header-actions" ref={actionsRef} />
    </header>
  );
}
