import type { TaskKind } from "../icons";
import type { Plugin } from "../../types";
import type { Authored, DraftView, Result } from "../templates/draft/types";
import { LIBRARY_FILE } from "../templates/draft/view";
import { SECTIONS, type PublishedComponent, type Section } from "./types";

/** The glyph a row draws: a gate's diamond, a node's or task's own icon, else the section's. */
export interface Glyph {
  gate?: boolean;
  icon?: string;
  taskKind?: TaskKind;
}

export interface Row {
  id: string;
  section: Section;
  name: string;
  glyph: Glyph;
  /** `3 chains`, `unused`, `unpublished` (no published counterpart), or "" until the published list arrives. */
  used: string;
  mark?: "add" | "change";
  problem: boolean;
  /** The plugin that ships it: read here and copied, never edited. Its name is the qualified one (`release:base`). */
  plugin?: Plugin;
}

const KINDS = new Set<string>(["agent", "builtin", "subprocess", "forge"]);
const SECTION_ICON: Partial<Record<Section, string>> = { steps: "layers", steering: "scroll-text" };

/** A task's kind: its own, else its `extends` target's, up the chain of bases (the draft has no resolved library, R18). */
function taskKind(tasks: Record<string, Authored>, entry: Authored): TaskKind | undefined {
  let at: Authored | undefined = entry;
  for (let i = 0; i < 8 && at; i++) {
    if (typeof at.kind === "string" && KINDS.has(at.kind)) return at.kind as TaskKind;
    at = typeof at.extends === "string" ? tasks[at.extends] : undefined;
  }
  return undefined;
}

const under = (path: string, id: string) => path === id || path.startsWith(`${id}.`);

type Sections = Record<string, Record<string, Authored> | undefined>;

/** The draft's library with the loaded plugins' components beside its own, by section: what a read of either
 *  follows `extends` through. A plugin's names are qualified (`release:base`), so none collides. */
export const withPlugins = (r: Result, plugins: DraftView["plugin_library"]): Sections => {
  const file = (r.model[LIBRARY_FILE] ?? {}) as Sections;
  return plugins ? { ...file, ...Object.fromEntries(SECTIONS.map((s) => [s, { ...file[s], ...plugins[s] }])) } : file;
};

/** The list's rows, grouped by section in file order, from the draft's model: a new or renamed component shows at once.
 *  The plugins' components follow each section's own. */
export function listRows(r: Result, published: PublishedComponent[] | null, plugins?: DraftView["plugin_library"]): Row[] {
  const file = withPlugins(r, plugins);
  const known = new Map((published ?? []).map((c) => [c.id, c]));
  return SECTIONS.flatMap((section) =>
    Object.entries(file[section] ?? {}).map(([name, entry]): Row => {
      const id = `${section}.${name}`;
      const e = entry ?? {};
      const pub = known.get(id);
      const shipped = !!plugins?.[section] && name in plugins[section];
      const n = pub?.used_by.length ?? 0;
      const change = r.changes.find((c) => under(c.path, id));
      const glyph: Glyph =
        section === "nodes" ? (e.kind === "gate" ? { gate: true } : { icon: typeof e.icon === "string" ? e.icon : undefined })
        : section === "tasks" ? { icon: typeof e.icon === "string" ? e.icon : undefined, taskKind: taskKind(file.tasks ?? {}, e) }
        : { icon: typeof e.icon === "string" ? e.icon : SECTION_ICON[section] };
      return {
        id,
        section,
        name,
        glyph,
        used: published === null || (shipped && !pub) ? "" : !pub ? "unpublished" : n === 0 ? "unused" : `${n} chain${n === 1 ? "" : "s"}`,
        mark: change ? (change.path === id && change.kind === "add" ? "add" : "change") : undefined,
        problem: r.problems.some((p) => p.component === id || under(p.path, id)),
        // Until the published list names its plugin, the namespace stands in for it.
        ...(shipped ? { plugin: pub?.plugin ?? { id: name.split(":")[0], version: "" } } : {}),
      };
    }),
  );
}

/** Rows a search and the kind filter leave. */
export const shownRows = (rows: Row[], q: string, hidden: ReadonlySet<Section>): Row[] => {
  const needle = q.trim().toLowerCase();
  return rows.filter((x) => !hidden.has(x.section) && (!needle || x.name.toLowerCase().includes(needle)));
};
