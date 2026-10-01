/** Syntax classes for the review diff (W8 C, R22): a small hand-written
 *  scanner per language family, no highlighter dependency. Five classes
 *  (GAP §1.1); the CSS maps them onto W1 status tokens, so the default scheme
 *  follows the colour amount. */

export type TokenClass = "kw" | "str" | "com" | "num" | "name";
export interface Token {
  text: string;
  cls?: TokenClass;
}
export type Lang = "clike" | "python" | "shell" | "yaml" | "markdown" | "plain";

const EXT: Record<string, Lang> = {};
for (const [lang, exts] of Object.entries({
  clike: "ts tsx js jsx mjs cjs json go rs java kt swift c h cpp cs css scss",
  python: "py",
  shell: "sh bash zsh",
  yaml: "yaml yml toml",
  markdown: "md",
})) for (const e of exts.split(" ")) EXT[e] = lang as Lang;

export function languageOf(path: string): Lang {
  const name = path.split("/").pop() ?? "";
  if (name === "Dockerfile" || name === "justfile") return "shell";
  const dot = name.lastIndexOf(".");
  return (dot > 0 && EXT[name.slice(dot + 1).toLowerCase()]) || "plain";
}

const words = (s: string) => new Set(s.split(" "));
const KEYWORDS: Record<Lang, Set<string>> = {
  clike: words(
    "const let var function return if else for while do switch case break continue new class extends implements interface type enum import export from default async await try catch finally throw typeof instanceof in of void null undefined true false this super static public private protected readonly abstract package func go defer chan map range struct fn impl pub mut use mod match loop where self Self trait let int long float double bool boolean string char byte nil",
  ),
  python: words("def class return if elif else for while in not and or is None True False import from as with try except finally raise lambda yield pass break continue global nonlocal async await assert del"),
  shell: words("if then else elif fi for in do done case esac while until function return local export set unset source"),
  yaml: words("true false null yes no on off"),
  markdown: new Set(),
  plain: new Set(),
};
/** After one of these, the next identifier is a definition's name. */
const DEFINERS = words("def class function fn func interface type struct enum trait");

/** Open across lines: inside a block comment or a multi-line string, and what closes it. */
export type ScanState = { cls: "com" | "str"; end: string } | null;

const ID = /[A-Za-z_$][\w$]*/y;
const NUM = /(?:0[xX][\dA-Fa-f_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?)\b/y;

/** One line, given what the line before left open. */
export function tokenizeLine(line: string, lang: Lang, state: ScanState): { tokens: Token[]; state: ScanState } {
  const out: Token[] = [];
  const push = (text: string, cls?: TokenClass) => {
    if (!text) return;
    const last = out[out.length - 1];
    if (last && last.cls === cls) last.text += text;
    else out.push(cls ? { text, cls } : { text });
  };
  if (lang === "plain") return { tokens: line ? [{ text: line }] : [], state: null };
  if (lang === "markdown" && !state && /^#{1,6}\s/.test(line)) return { tokens: [{ text: line, cls: "name" }], state: null };

  let i = 0;
  // Close what the line before left open.
  const close = (st: NonNullable<ScanState>): boolean => {
    const at = line.indexOf(st.end, i);
    if (at < 0) {
      push(line.slice(i), st.cls);
      i = line.length;
      return false;
    }
    push(line.slice(i, at + st.end.length), st.cls);
    i = at + st.end.length;
    return true;
  };
  if (state && !close(state)) return { tokens: out, state };

  let prevWord = "";
  const yamlKey = lang === "yaml" ? /^(\s*(?:-\s+)?)([\w.-]+)(?=\s*[:=])/.exec(line) : null;
  if (yamlKey && i === 0) {
    push(yamlKey[1]);
    push(yamlKey[2], "name");
    i = yamlKey[0].length;
  }
  while (i < line.length) {
    const rest = line.slice(i);
    const c = line[i];
    const prev = i > 0 ? line[i - 1] : " ";
    // Line comments.
    if ((lang === "clike" && rest.startsWith("//")) || (lang !== "clike" && lang !== "markdown" && c === "#" && /\s/.test(prev))) {
      push(rest, "com");
      break;
    }
    // Block comments and multi-line strings: may stay open past this line.
    const open = (lang === "clike" && rest.startsWith("/*") && { cls: "com" as const, start: "/*", end: "*/" }) ||
      (lang === "markdown" && rest.startsWith("<!--") && { cls: "com" as const, start: "<!--", end: "-->" }) ||
      (lang === "python" && (rest.startsWith('"""') || rest.startsWith("'''")) && { cls: "str" as const, start: rest.slice(0, 3), end: rest.slice(0, 3) }) ||
      (lang === "clike" && c === "`" && { cls: "str" as const, start: "`", end: "`" });
    if (open) {
      push(open.start, open.cls);
      i += open.start.length;
      const st = { cls: open.cls, end: open.end };
      if (!close(st)) return { tokens: out, state: st };
      continue;
    }
    if (lang === "markdown") {
      push(c);
      i++;
      continue;
    }
    // One-line strings; an unterminated one runs to the end of the line.
    if (c === '"' || c === "'") {
      let j = i + 1;
      while (j < line.length && line[j] !== c) j += line[j] === "\\" ? 2 : 1;
      push(line.slice(i, j + 1), "str");
      i = j + 1;
      continue;
    }
    if (!/[\w$]/.test(prev) || i === 0) {
      NUM.lastIndex = i;
      const n = NUM.exec(line);
      if (n) {
        push(n[0], "num");
        i += n[0].length;
        continue;
      }
    }
    ID.lastIndex = i;
    const id = ID.exec(line);
    if (id) {
      const w = id[0];
      push(w, KEYWORDS[lang].has(w) ? "kw" : DEFINERS.has(prevWord) ? "name" : undefined);
      prevWord = w;
      i += w.length;
      continue;
    }
    push(c);
    i++;
  }
  return { tokens: out, state: null };
}

/** One side of one hunk, in order: what a line leaves open carries to the next.
 *  ponytail: each hunk starts as code, so a hunk that begins inside a string or
 *  block comment is coloured wrong until it closes; fixing it needs a lookback
 *  into the file above the hunk, which the API does not serve. */
export function tokenizeSide(lines: string[], lang: Lang): Token[][] {
  let state: ScanState = null;
  return lines.map((l) => {
    const r = tokenizeLine(l, lang, state);
    state = r.state;
    return r.tokens;
  });
}
