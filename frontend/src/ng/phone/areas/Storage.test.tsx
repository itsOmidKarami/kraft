import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { CATEGORIES, NOTHING_TO_CLEAN } from "../../settings/storageWords";
import { StorageScreen } from "./Storage";
import { mountAt } from "./testkit";

const GB = 1024 ** 3;
const usage = (over: Record<string, unknown> = {}) => ({
  used_bytes: 12 * GB, quota_bytes: 8 * GB, limit_bytes: 10 * GB, state: "held", measured_at: "2026-10-01T10:00:00Z", reclaimable_bytes: 3 * GB,
  categories: { worktrees: 12 * GB, logs: GB / 2 },
  items: [{ id: "w1", title: "Old", status: "done", archived: false, bytes: 3 * GB, documents_bytes: 0, reclaimable: true, updated_at: "2026-09-01T10:00:00Z" }],
  orphans: [],
  ...over,
});
const PREVIEW = { items: [{ id: "w1", title: "Old", archivable: true, uncommitted_files: 2 }], used_after_bytes: 9 * GB, state_after: "ok" };
const open = (u = usage()) =>
  mountAt(<StorageScreen />, "/settings/storage", "/settings/storage", { "GET /storage": [200, u], "POST /storage/preview": [200, PREVIEW], "POST /work-items/bulk": [200, { results: [{ id: "w1", ok: true }] }] });

describe("Storage (phone)", () => {
  it("shows the usage against the quota and limit, the desktop's categories and its state note", async () => {
    open();
    expect(await screen.findByText("worktrees use")).toBeInTheDocument();
    expect(screen.getByText("Over the limit: starts are held until you make room.")).toBeInTheDocument();
    const where = screen.getByRole("region", { name: "Where it goes" });
    for (const [, label] of CATEGORIES) expect(within(where).getByText(label)).toBeInTheDocument();
    expect(within(where).getByText("12G")).toBeInTheDocument();
  });

  it("previews a clean-up, says what is lost, and archives only on confirm", async () => {
    const { calls } = open();
    await userEvent.click(await screen.findByText(/^Clean up all reclaimable \(1, 3G\)/));
    expect(calls.at(-1)).toMatchObject({ path: "/storage/preview", body: { ids: ["w1"] } });
    const sheet = await screen.findByRole("dialog", { name: "Clean up finished worktrees?" });
    expect(await within(sheet).findByText(/2 uncommitted files will be lost/)).toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/work-items/bulk"))).toBe(false);
    await userEvent.click(within(sheet).getByRole("button", { name: "Archive them" }));
    await waitFor(() => expect(calls.find((c) => c.path.startsWith("/work-items/bulk"))?.body).toEqual({ action: "archive", ids: ["w1"] }));
  });

  it("offers no clean-up when no finished item holds a worktree", async () => {
    open(usage({ items: [], reclaimable_bytes: 0 }));
    expect(await screen.findByText(NOTHING_TO_CLEAN)).toBeInTheDocument();
    expect(screen.getByText(/^Clean up all reclaimable/).closest("button")).toBeDisabled();
  });
});
