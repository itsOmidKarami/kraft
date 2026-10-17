import { useEffect, useState } from "react";
import { HIGHLIGHT_MAX_LINES, highlightFile, highlightLines, type FileTokens, type Token } from "./highlight";
import type { LangId } from "./languages";
import type { PatchFile } from "./patch";

/** A file's tokens, once its grammar has loaded; `null` until then, past the size cap, or on a failure.
 *  A result is kept with the file it was made from, so one that lands late never paints another file's rows. */
export function useFileTokens(file: PatchFile, lang: LangId | null): FileTokens | null {
  const [made, setMade] = useState<{ file: PatchFile; tokens: FileTokens } | null>(null);
  useEffect(() => {
    if (!lang) return;
    let live = true;
    highlightFile(file, lang).then((tokens) => live && tokens && setMade({ file, tokens }));
    return () => {
      live = false;
    };
  }, [file, lang]);
  return made?.file === file ? made.tokens : null;
}

/** A block of fenced code's tokens, once its grammar has loaded; `null` until then, past the size cap, or on a failure.
 *  Kept with the text it was made from, as `useFileTokens` keeps its file. */
export function useLineTokens(text: string, lang: LangId | null): Token[][] | null {
  const [made, setMade] = useState<{ text: string; lang: LangId; tokens: Token[][] } | null>(null);
  useEffect(() => {
    const lines = text.split("\n");
    if (!lang || lines.length > HIGHLIGHT_MAX_LINES) return;
    let live = true;
    highlightLines(lines, lang).then((tokens) => live && tokens && setMade({ text, lang, tokens }));
    return () => {
      live = false;
    };
  }, [text, lang]);
  return made?.text === text && made.lang === lang ? made.tokens : null;
}
