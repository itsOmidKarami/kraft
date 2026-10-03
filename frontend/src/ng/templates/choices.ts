import type { Choice } from "../ui/Combobox";
import type { Harnesses, LibraryComponent } from "../../types";

/** The library's steering profiles, each with its instructions' first line. */
export const steeringChoices = (library: LibraryComponent[] | string): Choice[] =>
  !Array.isArray(library) ? [] : library.filter((c) => c.kind === "steering").map((c) => ({ value: c.name, summary: firstLine(c.definition.instructions) }));

/** The harness profiles `harnesses.yaml` declares, each with its provider. */
export const harnessChoices = (h: Harnesses | null): Choice[] => (h?.profiles ?? []).map((p) => ({ value: p.id, summary: p.provider ? `on ${p.provider}` : undefined }));

const firstLine = (v: unknown) => (typeof v === "string" ? v.trim().split("\n")[0] || undefined : undefined);
