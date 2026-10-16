import { globalShortcuts, useRegisteredShortcuts, type ShortcutRow } from "../keys";
import { Dialog } from "../ui/Dialog";
import { Kbd } from "../ui/Kbd";

const List = ({ rows }: { rows: ShortcutRow[] }) => (
  <dl className="ng-shortcuts-list">
    {rows.map((s) => (
      <div key={s.does} className="ng-shortcut">
        <dt>{s.keys.map((k) => <Kbd key={k}>{k}</Kbd>)}</dt>
        <dd>{s.does}</dd>
      </div>
    ))}
  </dl>
);

/** The one shortcut help, opened by "?" on any page: the keys every page has, then the current page's. */
export function ShortcutHelp({ onClose }: { onClose: () => void }) {
  const pages = useRegisteredShortcuts();
  return (
    <Dialog title="Keyboard shortcuts" onClose={onClose} className="ng-shortcuts">
      <List rows={globalShortcuts()} />
      {pages.map((p) => (
        <section key={p.page} aria-label={p.page}>
          <h3 className="ng-shortcuts-page">{p.page}</h3>
          <List rows={p.rows} />
        </section>
      ))}
    </Dialog>
  );
}
