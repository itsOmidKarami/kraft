import { useEffect, useState } from "react";
import * as api from "../../api";
import type { Choice } from "../ui/Combobox";

/** The repo's markdown files, newest first, as the choices of an attach box.
 *  Fetched fresh whenever the picker opens (a file written a second ago is
 *  there) and again if the repo changes; empty before the answer and when it
 *  fails, so a pasted path still works. */
export function useRepoFiles(repo: string, open: boolean): Choice[] {
  const [files, setFiles] = useState<Choice[]>([]);
  useEffect(() => {
    if (!open || !repo) return;
    let current = true;
    api.repoFiles(repo).then(
      (r) => current && setFiles(r.files.map((f) => ({ value: f.path }))),
      () => current && setFiles([]),
    );
    return () => {
      current = false;
    };
  }, [repo, open]);
  return files;
}

/** The first choice `typed` names, by the rule `Combobox` filters with. */
export const firstMatch = (choices: Choice[], typed: string): string | undefined => {
  const q = typed.trim().toLowerCase();
  return choices.find((c) => c.value.toLowerCase().includes(q))?.value;
};
