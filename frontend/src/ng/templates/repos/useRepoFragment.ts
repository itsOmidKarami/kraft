import { useEffect, useState } from "react";
import { fragment } from "../draft/draftApi";
import type { ConfigDraft } from "../draft/useConfigDraft";

/** One repo's YAML (A.5), refetched after each answer that could change it. */
export function useRepoFragment(draft: ConfigDraft, path: string, on: boolean) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stamp = draft.view?.files["repos.yaml"];
  useEffect(() => {
    if (!on) return;
    let live = true;
    void fragment("repos", "repos", path).then((a) => {
      if (!live) return;
      if (a.status === 200) {
        setText(a.body.text);
        setError(null);
      } else {
        setText(null);
        setError("No YAML for this repo yet: it is not in the file until published.");
      }
    });
    return () => void (live = false);
  }, [on, path, stamp]);
  return { text, error };
}
