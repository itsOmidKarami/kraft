import { createContext } from "react";
import type { Plugin } from "../../types";
import { pluginLabel } from "../library/types";
import "../library/library.css";

/** True under a plugin's chain or component: the server answers 409 to every write, so the
 *  canvases and panes show their values and offer no control that would send one. */
export const ReadOnly = createContext(false);

/** `release@acme 1.4.0`, on whatever a plugin ships. */
export const PluginBadge = ({ plugin }: { plugin: Plugin }) => <span className="lib-chip" data-allow-ellipsis title={`From plugin ${pluginLabel(plugin)}: read-only`}>{pluginLabel(plugin)}</span>;
