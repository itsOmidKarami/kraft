import { useState, type ReactNode } from "react";
import type { StoragePreview, StorageUsage } from "../../types";
import { HeaderActionsHost } from "../shell/HeaderActions";

/** Stands in for the shell's header, so a page's header buttons render. */
export function WithHeader({ children }: { children: ReactNode }) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  return (
    <>
      <div ref={setHost} />
      <HeaderActionsHost.Provider value={host}>{children}</HeaderActionsHost.Provider>
    </>
  );
}

export const GB = 1024 ** 3;
const DAY = 86_400_000;

/** `GET /storage` for an instance that is over its limit: 12G used of a 10G limit and 8G quota, three live-or-finished items, one orphan. */
export function storageUsage(over: Partial<StorageUsage> = {}): StorageUsage {
  return {
    measured_at: new Date(Date.now() - 120_000).toISOString(),
    state: "held",
    used_bytes: 12 * GB,
    quota_bytes: 8 * GB,
    limit_bytes: 10 * GB,
    reclaimable_bytes: 3 * GB,
    categories: { worktrees: 11 * GB, sandboxes: GB, logs: 200 * 1024 ** 2, results: 0, databases: 50 * 1024 ** 2, attachments: 0, other: 0 },
    items: [
      { id: "w3", title: "Rate limiter", status: "active", archived: false, bytes: 6 * GB, updated_at: new Date(Date.now() - DAY).toISOString(), reclaimable: false },
      { id: "w1", title: "Cache embeddings", status: "completed", archived: false, bytes: 2 * GB, updated_at: new Date(Date.now() - 3 * DAY).toISOString(), reclaimable: true },
      { id: "w4", title: "Old spike", status: "abandoned", archived: false, bytes: GB, updated_at: new Date(Date.now() - 9 * DAY).toISOString(), reclaimable: true },
    ],
    orphans: [{ name: "7f3a", bytes: 512 * 1024 ** 2 }],
    ...over,
  };
}

/** `POST /storage/preview` for `w1` and `w4` of `storageUsage()`: 12G falls to 9G, still over the quota. */
export function storagePreview(over: Partial<StoragePreview> = {}): StoragePreview {
  return {
    freed_bytes: 3 * GB,
    used_after_bytes: 9 * GB,
    state_after: "over_quota",
    items: [
      { id: "w1", title: "Cache embeddings", bytes: 2 * GB, archivable: true, refusal: null, uncommitted_files: 3, unpushed_commits: 0, branch_kept: false },
      { id: "w4", title: "Old spike", bytes: GB, archivable: true, refusal: null, uncommitted_files: 0, unpushed_commits: 2, branch_kept: true },
    ],
    ...over,
  };
}

/** `POST /work-items/bulk` when every id was archived. */
export const bulkOk = (...ids: string[]): [number, unknown] => [200, { results: ids.map((id) => ({ id, ok: true, status: "completed" })) }];
