import { mod } from "../keys";
import { Dialog } from "../ui/Dialog";
import { Kbd } from "../ui/Kbd";

/** Every key the board answers to today, as the code binds it: BoardPage (`/`, `?`, Esc, ↑/↓), Row (Space, ⌘↵), the shell (⌘K, ⌘\). */
const SHORTCUTS = (): { keys: string[]; does: string }[] => [
  { keys: ["/"], does: "Focus the filter" },
  { keys: ["↑", "↓"], does: "Move between rows" },
  { keys: ["Space"], does: "Select or unselect the row" },
  { keys: [mod("↵")], does: "Open the row's full page" },
  { keys: ["Esc"], does: "Close the side panel" },
  { keys: [mod("K")], does: "Search" },
  { keys: [mod("\\")], does: "Pin or collapse the sidebar" },
  { keys: ["?"], does: "Show this list" },
];

export function Shortcuts({ onClose }: { onClose: () => void }) {
  return (
    <Dialog title="Keyboard shortcuts" onClose={onClose} className="board-shortcuts">
      <dl className="board-shortcuts-list">
        {SHORTCUTS().map((s) => (
          <div key={s.does} className="board-shortcut">
            <dt>{s.keys.map((k) => <Kbd key={k}>{k}</Kbd>)}</dt>
            <dd>{s.does}</dd>
          </div>
        ))}
      </dl>
    </Dialog>
  );
}
