import { ago, plural, usd } from "../../format";

/** A one-off chain's custom content, as the editor and the Start review show it.
 *  Pure: the desktop review, the new-item page's create review and the phone sheet all read it. */
export interface CustomPart {
  /** Where in the authored chain (`add[0]`, `overrides.tests…`); empty for the whole chain. */
  path: string;
  /** One line saying what it is. */
  what: string;
  value: unknown;
}

export interface CustomRow {
  key: string;
  /** What it is and where it sits, in words: the raw path (`add[0].node`) is not shown. */
  what: string;
  /** The part in full: a prompt or command as written, anything else as JSON. */
  text: string;
}

const scalar = (v: unknown) => (typeof v === "string" ? (/^[\w./@-]+$/.test(v) ? v : JSON.stringify(v)) : JSON.stringify(v));

/** A part as indented text, a multi-line string as a `|` block, so a prompt reads as written. Display only: nothing parses it. */
function indent(value: unknown, pad: string): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((v) => {
      const inner = indent(v, `${pad}  `);
      return v !== null && typeof v === "object" && inner.length ? [`${pad}- ${inner[0].slice(pad.length + 2)}`, ...inner.slice(1)] : [`${pad}- ${scalar(v)}`];
    });
  }
  if (value !== null && typeof value === "object") {
    return Object.entries(value).flatMap(([k, v]) => {
      if (typeof v === "string" && v.includes("\n")) return [`${pad}${k}: |`, ...v.replace(/\n$/, "").split("\n").map((l) => `${pad}  ${l}`)];
      if (v !== null && typeof v === "object" && (Array.isArray(v) ? v.length : Object.keys(v).length)) return [`${pad}${k}:`, ...indent(v, `${pad}  `)];
      return [`${pad}${k}: ${v !== null && typeof v === "object" ? (Array.isArray(v) ? "[]" : "{}") : scalar(v)}`];
    });
  }
  return [`${pad}${scalar(value)}`];
}

export const customText = (value: unknown): string => (typeof value === "string" ? value : indent(value, "").join("\n"));

export const customRows = (custom: CustomPart[]): CustomRow[] =>
  custom.map((p, i) => ({ key: `${i}:${p.path}`, what: p.what, text: customText(p.value) }));

/** What every review of a one-off chain says first: the create review, the Start review and the sheet on a phone. */
export const reviewSentence = (base: string | null, custom: boolean) =>
  `This item runs a chain of its own${base ? `, extending ${base}` : ""}.${custom ? " Its agents do what the custom parts below say: read them before you approve." : ""}`;

export type AddedCheck = { node: string; harnesses: string[]; estimate_usd: number | null; estimate_runs: number | null };

/** What the checks say about a node an item adds: which harnesses it runs and what it should cost. */
export const addedCheckText = (a: AddedCheck) =>
  `${a.node}: ${a.harnesses.length === 0 ? "runs no agent" : a.harnesses.length === 1 ? `harness ${a.harnesses[0]} is allowed` : `harnesses ${a.harnesses.join(", ")} are allowed`} · ${a.estimate_usd == null ? "no estimate yet" : `≈ +${usd(a.estimate_usd)} (avg of ${plural(a.estimate_runs ?? 1, "run")})`}`;

/** `GET /work-items/{id}/one-off`: what a person reads before approving. */
export interface OneOffReviewData {
  extends: string | null;
  custom: CustomPart[];
  provenance: { parent: string; nodes: Record<string, "inherited" | "added" | "overridden">; overrides: Record<string, string[]>; removed: string[] } | null;
  /** The review digest: what an approval names. */
  digest: string;
  needs_review: boolean;
  added: AddedCheck[];
  /** Each time a draft revised the chain after filing: the revision as lines (`templates.revision.diff`). */
  redrafted: { diff: string[] }[];
}

/** The nodes by what this chain did to them, from its provenance. */
export function changedNodes(p: OneOffReviewData["provenance"]) {
  const ids = (o: string) => Object.entries(p?.nodes ?? {}).filter(([, v]) => v === o).map(([k]) => k);
  return { added: ids("added"), overridden: ids("overridden"), removed: p?.removed ?? [] };
}

/** The same, from the new-item editor's answer (`result.extending`), before the item exists. */
export const changedFromExtending = (e: { origins: Record<string, string>; removed: { id: string }[] } | null | undefined) =>
  changedNodes(e ? { parent: "", nodes: e.origins as Record<string, "inherited" | "added" | "overridden">, overrides: {}, removed: e.removed.map((r) => r.id) } : null);

/** Approve what was shown: the digest of this read, the door it came through. */
export const approveBody = (r: OneOffReviewData, door: "web" | "phone") => ({ digest: r.digest, door });

const DOOR: Record<string, string> = { web: "in the web UI", phone: "on a phone" };

/** Who approved a one-off chain's custom content, through which door, and when; "just now" never breaks across lines. */
export const approvalLine = (a: { by: string; door: string; at: string } | null, now = Date.now()) =>
  a ? `approved by ${a.by === "human" ? "you" : a.by} ${DOOR[a.door] ?? `through the ${a.door}`} · ${ago(a.at, now).replace(/ /g, "\u00a0")}` : "custom content, not approved yet";
