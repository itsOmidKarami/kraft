import { useState } from "react";
import { detailOf } from "../http";
import type { ConfigDraft } from "../templates/draft/useConfigDraft";
import type { Op } from "../templates/draft/types";
import type { EntryView } from "./model";

/** `set_profile`'s patch for one provider entry: the entry replaces, `null` removes it. */
export const entryOp = (profile: string, provider: string, entry: EntryView | null): Op => ({
  op: "set_profile",
  name: profile,
  patch: { providers: { [provider]: entry === null ? null : { model: entry.model ?? "", ...(entry.effort ? { effort: entry.effort } : {}) } } },
});

/** Runs one op and keeps a refusal's text for the pane to show where the person is looking. */
export function useRun(draft: ConfigDraft) {
  const [error, setError] = useState<string | null>(null);
  const run = async (op: Op) => {
    const a = await draft.ops([op], { quiet: true });
    const ok = a.status >= 200 && a.status < 300;
    setError(ok ? null : detailOf(a.body));
    return { ok, answer: a };
  };
  return { run, error, clear: () => setError(null) };
}
