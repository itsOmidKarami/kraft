import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import type { Case } from "./cellKit";
import type { Flow } from "./flowKit";

const DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "cases");
const wave = (f: string) => f.replace(/(\.flows)?\.ts$/, "");

/**
 * Every sweep/cases/<wave>.ts (cells) or <wave>.flows.ts (flows), `shipped` first, then by wave number
 * (ux2-W2 before ux2-W10). A screen's first case in this order also gets the ~light cell at 1280, so keep
 * one screen's cases in one file.
 */
async function load<T>(flows: boolean, key: "cells" | "flows"): Promise<T[]> {
  const files = fs.readdirSync(DIR)
    .filter((f) => f.endsWith(".ts") && f.endsWith(".flows.ts") === flows)
    .sort((a, b) => (wave(a) === "shipped" ? -1 : wave(b) === "shipped" ? 1 : wave(a).localeCompare(wave(b), "en", { numeric: true })));
  const all: T[] = [];
  for (const f of files) {
    const mod = await import(pathToFileURL(path.join(DIR, f)).href);
    if (!Array.isArray(mod[key])) throw new Error(`sweep/cases/${f} must export \`${key}\` (an array)`);
    all.push(...mod[key]);
  }
  return all;
}

export const loadCells = () => load<Case>(false, "cells");
export const loadFlows = () => load<Flow>(true, "flows");
