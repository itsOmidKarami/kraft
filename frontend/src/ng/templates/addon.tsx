import { createContext } from "react";
import { Package } from "lucide-react";
import type { Addon } from "../../types";
import { addonLabel, addonRepo, inheritanceText, inheritanceTitle } from "../library/types";
import "../library/library.css";
import { Badge } from "../ui/Badge";

/** True under an add-on's chain or component: the server answers 409 to every write, so the
 *  canvases and panes show their values and offer no control that would send one. */
export const ReadOnly = createContext(false);

/** `release@acme 1.4.0` on whatever an add-on ships; `repo library app` on a repo library's,
 *  which is edited in that repository's `.kraft/`. */
export const AddonBadge = ({ addon }: { addon: Addon }) => {
  const repo = addonRepo(addon);
  const title = repo ? `From ${repo}'s repo library: read-only` : `From add-on ${addonLabel(addon)}: read-only`;
  return <Badge className="addon-badge" data-allow-ellipsis title={title}>
    <Package className="addon-badge-glyph" size={12} aria-hidden />
    <span className="addon-badge-text">{addonLabel(addon)}</span>
  </Badge>;
};

/** Beside an `AddonBadge`: "inherited from company", "overridden here" or "added here" on a child add-on's entry.
 *  Where the row is tight it ends in an ellipsis, so its title carries the whole mark above its author; `inline`, in
 *  a picker's second line, it wraps with that line instead. */
export const InheritanceMark = ({ addon, inline }: { addon: Addon; inline?: boolean }) => {
  const text = inheritanceText(addon);
  return text ? <span className={`inheritance-mark is-${addon.inheritance!.origin}${inline ? " is-inline" : ""}`} data-allow-ellipsis title={`${text}\n${inheritanceTitle(addon)}`} aria-description={inheritanceTitle(addon) ?? undefined}>{text}</span> : null;
};
