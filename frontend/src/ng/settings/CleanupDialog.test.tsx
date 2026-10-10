import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import * as bulk from "../board/bulk";
import { useBulk, type BulkOutcome } from "../board/bulk";
import { stubFetch } from "../item/testkit";
import { CleanupDialog } from "./CleanupDialog";
import { bulkOk, GB, storagePreview, storageUsage } from "./testkit";

type Answers = Parameters<typeof stubFetch>[0];

function mount(answers: Answers, ids = ["w1", "w4"]) {
  const calls = stubFetch(answers);
  const onClose = vi.fn();
  const onDone = vi.fn();
  render(<CleanupDialog ids={ids} usage={storageUsage()} onClose={onClose} onDone={onDone} />);
  return { calls, onClose, onDone, sent: () => calls.filter((c) => c.path === "/work-items/bulk") };
}

beforeEach(() => {
  useBulk.setState({ last: null });
  vi.spyOn(api, "listWorkItems").mockResolvedValue({ items: [], cursor: 1 });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("CleanupDialog", () => {
  it("says what is freed, what is lost and what stays before anything is removed", async () => {
    const { calls, sent } = mount({ "POST /storage/preview": [200, storagePreview()] });
    const dialog = await screen.findByRole("dialog", { name: "Clean up worktrees" });
    expect(await within(dialog).findByText(/Frees 3G/)).toBeInTheDocument();
    expect(within(dialog).getByText(/9G used afterwards: still over the quota of 8G, under the limit of 10G/)).toBeInTheDocument();
    const [cache, spike] = within(dialog).getAllByRole("listitem");
    expect(cache).toHaveTextContent("Cache embeddings");
    expect(cache).toHaveTextContent("2G");
    expect(cache).toHaveTextContent("3 uncommitted files will be lost");
    expect(cache).toHaveTextContent("branch is deleted");
    expect(spike).toHaveTextContent("no uncommitted files");
    expect(spike).toHaveTextContent("branch stays: it has commits nothing else holds");
    expect(within(dialog).getByText(/Each item moves to Archived\. You can restore it there as a record; its worktree is not brought back\./)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/storage/preview")?.body).toEqual({ ids: ["w1", "w4"] });
    expect(sent()).toEqual([]);
  });

  it.each([
    ["ok", "under the quota of 8G"],
    ["held", "still over the limit of 10G, so starts stay held"],
    [null, "9G used afterwards."],
  ] as const)("says where usage lands when the state after is %s", async (state_after, text) => {
    mount({ "POST /storage/preview": [200, storagePreview({ state_after })] });
    expect(await screen.findByText((content) => content.includes(text))).toBeInTheDocument();
  });

  it("archives the previewed ids on confirm, then reports back", async () => {
    const { sent, onClose, onDone } = mount({ "POST /storage/preview": [200, storagePreview()], "POST /work-items/bulk": bulkOk("w1", "w4") });
    await userEvent.click(await screen.findByRole("button", { name: "Archive 2 items and free 3G" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(sent().map((c) => c.body)).toEqual([{ action: "archive", ids: ["w1", "w4"] }]);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("will not archive when the preview failed", async () => {
    const { sent } = mount({ "POST /storage/preview": [500, { detail: "git status failed in w1" }] });
    expect(await screen.findByRole("alert")).toHaveTextContent("nothing was removed: git status failed in w1");
    expect(screen.queryByRole("listitem")).toBeNull();
    expect(screen.getByRole("button", { name: /^Archive/ })).toBeDisabled();
    expect(sent()).toEqual([]);
  });

  it("lists an item the preview refuses as skipped and sends only the others", async () => {
    const preview = storagePreview({
      freed_bytes: GB,
      used_after_bytes: 11 * GB,
      state_after: "held",
      items: [
        { id: "w1", title: "Cache embeddings", bytes: 2 * GB, archivable: false, refusal: "work item is already archived", uncommitted_files: null, unpushed_commits: 0, branch_kept: false },
        storagePreview().items[1],
      ],
    });
    const { sent } = mount({ "POST /storage/preview": [200, preview], "POST /work-items/bulk": bulkOk("w4") });
    expect(await screen.findByText("Skipped: work item is already archived")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Archive 1 item and free 1G" }));
    await waitFor(() => expect(sent()).toHaveLength(1));
    expect(sent()[0].body).toEqual({ action: "archive", ids: ["w4"] });
  });

  const failures: [string, [number, unknown], string, string[]][] = [
    [
      "someone archived one between the preview and the confirm",
      [200, { results: [{ id: "w1", ok: false, error: "work item is already archived" }, { id: "w4", ok: true, status: "abandoned" }] }],
      "1 of 2 archived",
      ["Cache embeddings: work item is already archived"],
    ],
    [
      "the server leaves an id out of its answer",
      [200, { results: [{ id: "w4", ok: true, status: "abandoned" }] }],
      "1 of 2 archived",
      ["Cache embeddings: not archived"],
    ],
    [
      "the bulk call is refused outright",
      [500, { detail: "database is locked" }],
      "0 of 2 archived",
      ["Cache embeddings: database is locked", "Old spike: database is locked"],
    ],
  ];
  it.each(failures)("keeps the dialog open and names what was not archived: %s", async (_name, answer, summary, lines) => {
    const { onClose, onDone } = mount({ "POST /storage/preview": [200, storagePreview()], "POST /work-items/bulk": answer });
    await userEvent.click(await screen.findByRole("button", { name: "Archive 2 items and free 3G" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(summary);
    for (const line of lines) expect(screen.getByText(line)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /^Archive/ })).toBeNull();
    expect(useBulk.getState().last).toBeNull();
    // Cancel unmounted: focus moves to Close, not to the page behind.
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("clears only its own parked answer, not a board failure parked meanwhile", async () => {
    const other: BulkOutcome = { action: "pause", ids: ["x"], error: "boom" };
    vi.spyOn(bulk, "sendBulk").mockImplementation(async (action, ids) => {
      const out: BulkOutcome = { action, ids, results: ids.map((id) => ({ id, ok: false, error: "nope" })) };
      useBulk.setState({ last: other });
      return out;
    });
    mount({ "POST /storage/preview": [200, storagePreview()] });
    await userEvent.click(await screen.findByRole("button", { name: "Archive 2 items and free 3G" }));
    await screen.findByRole("alert");
    expect(useBulk.getState().last).toBe(other);
  });
});
