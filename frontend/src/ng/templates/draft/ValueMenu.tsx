import { ChevronDown, Pencil } from "lucide-react";
import { Menu } from "../../ui/Menu";

export type Option = { value: string; label: string; disabled?: boolean };

/** ValueCell's idiom for a value picked from a list: the chosen option and a pencil, and a click opens the list.
 *  `plain` is a picker that is no edit: a chevron, not a pencil. */
export function ValueMenu({ label, value, options, onPick, bad, plain }: {
  label: string;
  value: string;
  options: Option[];
  onPick: (v: string) => void;
  bad?: boolean;
  plain?: boolean;
}) {
  const shown = options.find((o) => o.value === value)?.label ?? value;
  const Icon = plain ? ChevronDown : Pencil;
  return (
    <Menu
      label={label}
      triggerLabel={`${label}, ${shown}. ${plain ? "Choose" : "Edit"}`}
      triggerClass={`adr-val${bad ? " is-bad" : ""}`}
      trigger={<>{shown}<Icon size={12} className="adr-pen" aria-hidden /></>}
      items={options.map((o) => ({ label: o.label, checked: o.value === value, disabled: o.disabled, onSelect: () => onPick(o.value) }))}
    />
  );
}
