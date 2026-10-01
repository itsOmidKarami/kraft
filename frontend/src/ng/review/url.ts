import { useLocation, useNavigate } from "react-router-dom";
import type { CompareTarget, WorkItem } from "../../types";

/** Where the review page is (spec §6.4): what it compares, which nodes' files,
 *  the file in view, the gate it reviews, and whether the gate's document
 *  overlay is open. */
export interface ReviewPlace {
  from: CompareTarget;
  to: CompareTarget;
  /** null: every node. */
  nodes: string[] | null;
  file: string | null;
  gate: string | null;
  doc: boolean;
}

type Defaults = Pick<WorkItem, "last_review_sha" | "head_sha" | "pending_gate">;

const isTarget = (v: string | null): v is CompareTarget =>
  v === "base" || v === "latest" || v === "last_review" || (!!v && /^attempt:[1-9]\d*$/.test(v));

/** A re-review compares from the last review (prototype `openReview`); a first look from the base. */
export function defaultFrom(item: Defaults): CompareTarget {
  return item.last_review_sha && item.last_review_sha !== item.head_sha ? "last_review" : "base";
}

export function readReview(search: URLSearchParams, item: Defaults): ReviewPlace {
  const from = search.get("from");
  const to = search.get("to");
  const nodes = search.get("nodes");
  return {
    from: isTarget(from) && from !== "latest" ? from : defaultFrom(item),
    to: isTarget(to) ? to : "latest",
    nodes: nodes ? nodes.split(",").filter(Boolean) : null,
    file: search.get("file") || null,
    gate: search.get("gate") || item.pending_gate || null,
    doc: search.get("doc") === "1",
  };
}

/** The page's URL for a place; a value equal to its default is left out. */
export function reviewUrl(id: string, p: ReviewPlace, item: Defaults): string {
  const q = new URLSearchParams();
  if (p.from !== defaultFrom(item)) q.set("from", p.from);
  if (p.to !== "latest") q.set("to", p.to);
  if (p.nodes) q.set("nodes", p.nodes.join(","));
  if (p.file) q.set("file", p.file);
  if (p.gate && p.gate !== item.pending_gate) q.set("gate", p.gate);
  if (p.doc) q.set("doc", "1");
  const s = q.toString();
  return `/work-items/${encodeURIComponent(id)}/review${s ? `?${s}` : ""}`;
}

/** The place from the URL, and a setter that replaces it: moving around the
 *  review page is not history; entering and leaving it is (links push). */
export function useReviewPlace(item: WorkItem): [ReviewPlace, (patch: Partial<ReviewPlace>) => void] {
  const { search } = useLocation();
  const navigate = useNavigate();
  const place = readReview(new URLSearchParams(search), item);
  const set = (patch: Partial<ReviewPlace>) => navigate(reviewUrl(item.id, { ...place, ...patch }, item), { replace: true });
  return [place, set];
}
