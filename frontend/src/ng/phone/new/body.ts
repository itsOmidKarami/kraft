export type AttachKind = "spec" | "plan";
export interface NewDraft {
  title: string;
  brief: string;
  repo: string;
  chain: string;
  spec: string;
  plan: string;
}
export const EMPTY: NewDraft = { title: "", brief: "", repo: "", chain: "", spec: "", plan: "" };

/** `POST /work-items`' body: the same one the board's composer sends (new/body.test.ts pins the two together). */
export function createBody(d: NewDraft, autostart: boolean) {
  const attachments = (["spec", "plan"] as AttachKind[]).filter((k) => d[k].trim()).map((k) => ({ kind: k, path: d[k].trim() }));
  return { title: d.title.trim(), description: d.brief.trim(), repo: d.repo, chain_template: d.chain, attachments, autostart };
}

const KEY = "kraft.ng.phone.new";
/** The unsent draft survives a reload until it is created or discarded; storage may be unavailable. */
export function loadDraft(): NewDraft {
  try {
    return { ...EMPTY, ...(JSON.parse(sessionStorage.getItem(KEY) ?? "{}") as Partial<NewDraft>) };
  } catch {
    return EMPTY;
  }
}
export function saveDraft(d: NewDraft | null) {
  try {
    if (d && (d.title || d.brief)) sessionStorage.setItem(KEY, JSON.stringify(d));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* no storage: the draft is simply not kept */
  }
}

/** A pasted repo-relative path rather than a search. */
export const looksLikePath = (s: string) => /[/.]/.test(s) && !/\s/.test(s.trim());
