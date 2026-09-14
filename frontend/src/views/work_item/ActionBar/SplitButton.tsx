import { OverflowMenu } from "../../../components/ui";

/** A primary action with a caret beside it opening a one-entry menu for an
 *  alternate action (ESCALATION_THREADS_SPEC.md §3's "Escalate ⌘↵ | ▾").
 *  The caret reuses `OverflowMenu`'s own popover rather than a second
 *  dropdown implementation (Kraft-dkb6g) -- this component only pairs it
 *  with the primary button and the shared border. */
export function SplitButton({
  primaryLabel,
  onPrimary,
  disabled,
  menuLabel,
  menuHint,
  onMenuSelect,
}: {
  primaryLabel: string;
  onPrimary: () => void;
  disabled?: boolean;
  /** The menu's one row, e.g. "Escalate in new thread". */
  menuLabel: string;
  /** Its accessible description / hover title, e.g. "Starts without turns
   *  1–3. Documents, log and findings stay available." */
  menuHint: string;
  onMenuSelect: () => void;
}) {
  return (
    <div className="split-button">
      <button className="btn btn-primary split-button-primary" disabled={disabled} onClick={onPrimary}>
        {primaryLabel}
      </button>
      <OverflowMenu
        trigger="caret"
        label={`${primaryLabel} options`}
        items={[{ label: menuLabel, hint: menuHint, onSelect: onMenuSelect }]}
      />
    </div>
  );
}
