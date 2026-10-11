import { createContext } from "react";
import type { Addon } from "../../types";
import { addonLabel } from "../library/types";
import "../library/library.css";

/** True under an add-on's chain or component: the server answers 409 to every write, so the
 *  canvases and panes show their values and offer no control that would send one. */
export const ReadOnly = createContext(false);

/** `release@acme 1.4.0`, on whatever an add-on ships. */
export const AddonBadge = ({ addon }: { addon: Addon }) => <span className="lib-chip" data-allow-ellipsis title={`From add-on ${addonLabel(addon)}: read-only`}>{addonLabel(addon)}</span>;
