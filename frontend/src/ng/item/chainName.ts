import type { WorkItem } from "../../types";

/** The chain an item runs: the name it was filed with, else the frozen chain's own id (an item filed with no
 *  chain runs the repo's default), else "custom chain". The board, the item pane and the phone all say this. */
export const chainName = (i: Pick<WorkItem, "chain_template"> & { chain_definition?: { template_id?: string } | null }) =>
  i.chain_template || i.chain_definition?.template_id || "custom chain";

/** Why a node does not run on an item: an attached document stands in for it. */
export const coveredText = (kind: string): string => `covered by the attached ${kind}`;
