import type { WorkItem } from "./api";

/** A row token for each place an item links to: `mr` for its merge request,
 *  `attSpec` and `attPlan` for the documents it was filed with, in that order. */
export type LinkToken = "mr" | "attSpec" | "attPlan";

/** Only a web address opens in the browser: any other scheme could hand the
 *  URL to some other handler on this machine. */
export const isWebUrl = (url: string | null | undefined): url is string => !!url && /^https?:\/\//i.test(url);

export function linkTokens(item: Pick<WorkItem, "mr_ref" | "attachments">): LinkToken[] {
  const kinds = new Set((item.attachments ?? []).map((a) => a.kind));
  return [
    ...(isWebUrl(item.mr_ref?.url) ? ["mr" as const] : []),
    ...(kinds.has("spec") ? ["attSpec" as const] : []),
    ...(kinds.has("plan") ? ["attPlan" as const] : []),
  ];
}
