/** The library's four sections, in the order the list shows them (Decisions §10). */
export const SECTIONS = ["nodes", "steps", "tasks", "steering"] as const;
export type Section = (typeof SECTIONS)[number];
export const SECTION_LABEL: Record<Section, string> = { nodes: "Nodes", steps: "Steps", tasks: "Tasks", steering: "Steering" };

import type { Plugin } from "../../types";

/** One place a chain uses a component (`GET /templates/library`'s `used_by_paths`). */
export interface Use {
  chain: string;
  path: string;
  overrides: boolean;
  via?: string;
}

/** A component of the published `library.yaml`, with who uses it. */
export interface PublishedComponent {
  id: string;
  kind: Section;
  name: string;
  used_by: string[];
  used_by_paths: Use[];
  plugin?: Plugin | null;
}

export interface PublishedLibrary {
  file: string;
  text: string;
  components: PublishedComponent[];
}

/** `tasks.implementer` → its section and name. Names carry no dot (the id rule). */
export function parseRef(ref: string | undefined): { section: Section; name: string } | null {
  const [section, name, ...rest] = (ref ?? "").split(".");
  return name && !rest.length && (SECTIONS as readonly string[]).includes(section) ? { section: section as Section, name } : null;
}

/** `release@acme 1.4.0`: what a plugin's chain or component is badged with, on both layouts. */
export const pluginLabel = (p: Plugin) => `${p.id} ${p.version}`.trim();

export const refUrl = (id: string) => `/templates/library/${encodeURIComponent(id)}`;
