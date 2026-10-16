/** The library's four sections, in the order the list shows them (Decisions §10). */
export const SECTIONS = ["nodes", "steps", "tasks", "steering"] as const;
export type Section = (typeof SECTIONS)[number];
export const SECTION_LABEL: Record<Section, string> = { nodes: "Nodes", steps: "Steps", tasks: "Tasks", steering: "Steering" };

import type { Addon } from "../../types";

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
  addon?: Addon | null;
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

/** `release@acme 1.4.0`, or `repo library app`: what an add-on's or a repo
 *  library's chain or component is badged with, on both layouts. */
export const addonLabel = (p: Addon) => (addonRepo(p) ? `repo library ${addonRepo(p)}` : `${p.id} ${p.version}`.trim());

const REPO_LIBRARY = "library@repo:";

/** The repository `p` is the repo library of; null for an add-on. */
export const addonRepo = (p: Addon | null | undefined): string | null =>
  p?.repo ?? (p?.id.startsWith(REPO_LIBRARY) ? p.id.slice(REPO_LIBRARY.length) : null);

/** The chains an item in the repository `repoId` may run: the instance's own,
 *  add-ons', and that repository's own repo library, never another's
 *  (`addons/load.py` `scope_problem`; the VS Code extension's `chainsFor`).
 *  A repo with no id reads no repo library. */
export function chainsForRepo<C extends { addon?: Addon | null }>(chains: C[], repoId: string | null | undefined): C[] {
  return chains.filter((c) => {
    const owner = addonRepo(c.addon);
    return owner === null || (!!repoId && owner === repoId);
  });
}

/** A chain's option in a picker, desktop and phone alike: its id and size. */
export const chainOptionText = (c: { id: string; nodes: unknown[] }) => `${c.id} · ${c.nodes.length} nodes`;

/** The line under a chain picker that names the repo's default chain. */
export const defaultChainNote = (chain: string) => `${chain} is the repo's default chain`;

export const refUrl = (id: string) => `/templates/library/${encodeURIComponent(id)}`;
