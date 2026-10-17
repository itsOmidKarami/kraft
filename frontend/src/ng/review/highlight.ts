/** Syntax classes for the review diff: Shiki's TextMate grammars, folded onto
 *  five classes. The CSS maps the classes onto the colour scheme, so the
 *  default scheme follows the colour amount. Shiki and its grammars load on
 *  first use, as chunks, never in the main bundle. */
import type { HighlighterCore, ThemeRegistration } from "shiki/core";
import { LANGS, type LangId } from "./languages";
import type { PatchFile } from "./patch";

export type TokenClass = "kw" | "str" | "com" | "num" | "name";
export interface Token {
  text: string;
  cls?: TokenClass;
}

/** Per block (one hunk side, or one fenced block), more lines than this stay plain. Tokenizing runs on the main
 *  thread, one block per task: TSX, the slowest grammar measured, takes about 0.035 ms a line in Chromium, so this keeps one task near 35 ms, under the 50 ms a browser calls a long task. */
export const HIGHLIGHT_MAX_LINES = 1000;

/** Kraft's theme paints no colours, only markers: each stands for one class. */
const MARKER: Record<string, TokenClass> = { "#000001": "kw", "#000002": "str", "#000003": "num", "#000004": "com", "#000005": "name" };
const SCOPES: Record<string, string[]> = {
  "#000001": ["keyword", "storage", "keyword.control", "storage.type", "storage.modifier"],
  "#000002": ["string", "string.quoted", "string.template", "punctuation.definition.string"],
  "#000003": ["constant.numeric", "constant.language"],
  "#000004": ["comment", "punctuation.definition.comment"],
  "#000005": [
    "entity.name",
    "entity.name.function",
    "entity.name.type",
    "entity.name.class",
    "entity.name.tag.yaml",
    "support.type.property-name",
    "meta.object-literal.key",
    "markup.heading",
  ],
};
const KRAFT_THEME: ThemeRegistration = {
  name: "kraft",
  type: "dark",
  fg: "#000000",
  bg: "#ffffff",
  settings: [
    ...Object.entries(SCOPES).map(([foreground, scope]) => ({ scope, settings: { foreground } })),
    // Operators sit under `keyword` in most grammars; they read as text, not as words.
    { scope: ["keyword.operator"], settings: { foreground: "#000000" } },
  ],
};

let highlighter: Promise<HighlighterCore> | undefined;
const loaded = new Map<LangId, Promise<void>>();
const warned = new Set<LangId>();

const core = () =>
  (highlighter ??= (async () => {
    const [{ createHighlighterCore }, { createJavaScriptRegexEngine }] = await Promise.all([import("shiki/core"), import("@shikijs/engine-javascript")]);
    return createHighlighterCore({ themes: [KRAFT_THEME], langs: [], engine: createJavaScriptRegexEngine() });
  })());

const ready = (lang: LangId) => {
  let p = loaded.get(lang);
  if (!p) {
    p = Promise.all([core(), LANGS[lang]()]).then(([h, g]) => h.loadLanguage(g.default));
    loaded.set(lang, p);
    // A failed load is not retried; the file stays plain.
  }
  return p;
};

let queue: Promise<unknown> = Promise.resolve();
// Not scheduler.yield(): its continuation jumps the queue ahead of timers, which then wait out the whole page.
const yieldToBrowser = () => new Promise<void>((r) => setTimeout(r, 0));
/** Every tokenize runs through here: one block, then a yield, so a page of files never holds the main thread
 *  for more than one block at a time. (Awaiting a loaded grammar is a microtask; without this they all run back to back.) */
const turn = <T>(job: () => T): Promise<T> => {
  const run = queue.then(job);
  queue = run.then(yieldToBrowser, yieldToBrowser);
  return run;
};

/** One block of code, its lines in order: what a line leaves open carries to
 *  the next. `null` when the grammar or the tokenizer fails (logged once). */
export async function highlightLines(lines: string[], lang: LangId): Promise<Token[][] | null> {
  try {
    await ready(lang);
    if (!lines.length) return [];
    const h = await core();
    const out = await turn(() => h.codeToTokensBase(lines.join("\n"), { lang, theme: "kraft" }));
    return lines.map((line, i) => {
      const tokens: Token[] = [];
      const push = (text: string, cls?: TokenClass) => {
        if (!text) return;
        const last = tokens[tokens.length - 1];
        if (last && last.cls === cls) last.text += text;
        else tokens.push(cls ? { text, cls } : { text });
      };
      for (const t of out[i] ?? []) push(t.content, t.color ? MARKER[t.color.toLowerCase()] : undefined);
      // Shiki splits on \r\n and drops the \r; the row's text keeps it.
      if (line.endsWith("\r")) push("\r", tokens[tokens.length - 1]?.cls);
      return tokens;
    });
  } catch (e) {
    if (!warned.has(lang)) {
      warned.add(lang);
      console.warn(`Syntax highlighting for ${lang} failed; showing plain text.`, e);
    }
    return null;
  }
}

/** Per hunk, per line of that hunk's old (`kind !== "+"`) or new (`kind !== "-"`) side. */
export type FileTokens = { old: Token[][][]; new: Token[][][] };

/** Each hunk side is one block. A block past the line cap stays plain (`[]`) with no Shiki work; the file's
 *  other blocks are coloured. `null` on failure. */
export async function highlightFile(file: PatchFile, lang: LangId): Promise<FileTokens | null> {
  const sides = (keep: "+" | "-") => file.hunks.map((h) => h.lines.filter((l) => l.kind !== keep).map((l) => l.text));
  const run = async (blocks: string[][]) => {
    const r = await Promise.all(blocks.map((b) => (b.length > HIGHLIGHT_MAX_LINES ? [] : highlightLines(b, lang))));
    return r.every((x) => x) ? (r as Token[][][]) : null;
  };
  const [old, nu] = await Promise.all([run(sides("+")), run(sides("-"))]);
  return old && nu ? { old, new: nu } : null;
}
