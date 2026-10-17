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

/** Per file (both sides together), more lines than this stay plain. */
export const HIGHLIGHT_MAX_LINES = 5000;

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
  settings: Object.entries(SCOPES).map(([foreground, scope]) => ({ scope, settings: { foreground } })),
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

/** One block of code, its lines in order: what a line leaves open carries to
 *  the next. `null` when the grammar or the tokenizer fails (logged once). */
export async function highlightLines(lines: string[], lang: LangId): Promise<Token[][] | null> {
  try {
    await ready(lang);
    if (!lines.length) return [];
    const h = await core();
    const out = h.codeToTokensBase(lines.join("\n"), { lang, theme: "kraft" });
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

/** Each hunk side is one block. `null` past the line cap, or on failure. */
export async function highlightFile(file: PatchFile, lang: LangId): Promise<FileTokens | null> {
  const sides = (keep: "+" | "-") => file.hunks.map((h) => h.lines.filter((l) => l.kind !== keep).map((l) => l.text));
  const olds = sides("+");
  const news = sides("-");
  const total = [...olds, ...news].reduce((n, s) => n + s.length, 0);
  if (total > HIGHLIGHT_MAX_LINES) return null;
  const run = async (blocks: string[][]) => {
    const r = await Promise.all(blocks.map((b) => highlightLines(b, lang)));
    return r.every((x) => x) ? (r as Token[][][]) : null;
  };
  const [old, nu] = await Promise.all([run(olds), run(news)]);
  return old && nu ? { old, new: nu } : null;
}
