import { useEffect, useState } from "react";
import { highlightFile, type FileTokens } from "./highlight";
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
