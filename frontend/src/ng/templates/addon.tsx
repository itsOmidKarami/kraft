import { createContext } from "react";
import type { Addon } from "../../types";
import { addonLabel, addonRepo } from "../library/types";
import "../library/library.css";

/** True under an add-on's chain or component: the server answers 409 to every write, so the
 *  canvases and panes show their values and offer no control that would send one. */
export const ReadOnly = createContext(false);

/** `release@acme 1.4.0` on whatever an add-on ships; `repo library app` on a repo library's,
 *  which is edited in that repository's `.kraft/`. */
export const AddonBadge = ({ addon }: { addon: Addon }) => {
  const repo = addonRepo(addon);
  const title = repo ? `From ${repo}'s repo library: read-only` : `From add-on ${addonLabel(addon)}: read-only`;
  return <span className="lib-chip" data-allow-ellipsis title={title}>{addonLabel(addon)}</span>;
};
