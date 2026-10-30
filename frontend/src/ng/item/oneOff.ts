import { usd } from "../../format";

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
  what: string;
  where: string;
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
  custom.map((p, i) => ({ key: `${i}:${p.path}`, what: p.what, where: p.path || "the whole chain", text: customText(p.value) }));

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** What the checks say about a node an item adds: which harnesses it runs and what it should cost. */
export const addedCheckText = (a: { node: string; harnesses: string[]; estimate_usd: number | null; estimate_runs: number | null }) =>
  `${a.node}: ${a.harnesses.length === 0 ? "runs no agent" : `${plural(a.harnesses.length, "harness", "harnesses")} ${a.harnesses.join(", ")} ${plural(a.harnesses.length, "is", "are")} allowed`} · ${a.estimate_usd == null ? "no estimate yet" : `≈ +${usd(a.estimate_usd)} (avg of ${a.estimate_runs ?? 1} ${plural(a.estimate_runs ?? 1, "run", "runs")})`}`;

/** `GET /work-items/{id}/one-off`: what a person reads before approving. */
export interface OneOffReviewData {
  extends: string | null;
  custom: CustomPart[];
  provenance: { parent: string; nodes: Record<string, "inherited" | "added" | "overridden">; overrides: Record<string, string[]>; removed: string[] } | null;
  /** The review digest: what an approval names. */
  digest: string;
  needs_review: boolean;
  added: { node: string; harnesses: string[]; estimate_usd: number | null; estimate_runs: number | null }[];
  redrafted: { diff?: unknown; changes?: unknown[] }[];
}

/** The nodes by what this chain did to them, from its provenance. */
export function changedNodes(p: OneOffReviewData["provenance"]) {
  const ids = (o: string) => Object.entries(p?.nodes ?? {}).filter(([, v]) => v === o).map(([k]) => k);
  return { added: ids("added"), overridden: ids("overridden"), removed: p?.removed ?? [] };
}

/** Approve what was shown: the digest of this read, the door it came through. */
export const approveBody = (r: OneOffReviewData, door: "web" | "phone") => ({ digest: r.digest, door });
