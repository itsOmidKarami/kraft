import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { WorkItem } from "../../types";
import { ArchivedView } from "./Archived";
import { item } from "../../testFixtures";

const wi = (over: Partial<WorkItem>): WorkItem =>
  item({
    title: "Item", repo: "/repo-a", status: "completed", chain_template: "quick-task",
    chain_definition: { template_id: "quick-task", nodes: [] }, current_node_id: null, bead_id: null,
    archived_at: "2026-01-01T00:00:00Z", archived_by: "you",
    ...over,
  });

describe("ArchivedView", () => {
  it("lists archived items with ENDED AS and ARCHIVED columns", async () => {
    vi.spyOn(api, "listArchivedWorkItems").mockResolvedValue({
      items: [wi({ id: "w1", status: "completed", archived_by: "you" })],
      cursor: 1,
    });
    render(
      <MemoryRouter>
        <ArchivedView />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/completed/i)).toBeInTheDocument();
    expect(screen.getByText(/by you/i)).toBeInTheDocument();
  });

  it("Restore calls the API and removes the row from the list", async () => {
    const spy = vi
      .spyOn(api, "restoreWorkItem")
      .mockResolvedValue({ id: "w1", status: "completed" });
    vi.spyOn(api, "listArchivedWorkItems")
      .mockResolvedValueOnce({ items: [wi({ id: "w1", status: "completed" })], cursor: 1 })
      .mockResolvedValueOnce({ items: [], cursor: 2 });
    render(
      <MemoryRouter>
        <ArchivedView />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /restore/i }));
    expect(spy).toHaveBeenCalledWith("w1");
    await waitFor(() => expect(screen.queryByText("Item")).toBeNull());
  });

  it("an empty archive is one line with no header row, and sorts through the board's disclosure (W4.7)", async () => {
    vi.spyOn(api, "listArchivedWorkItems").mockResolvedValue({ items: [], cursor: 0 });
    render(
      <MemoryRouter>
        <ArchivedView />
      </MemoryRouter>,
    );
    expect(await screen.findByText("nothing here yet")).toBeInTheDocument();
    expect(screen.queryByText("WORK ITEM")).toBeNull();
    expect(document.querySelector("select")).toBeNull();
    expect(document.querySelector(".board-sort summary")?.textContent).toContain("Sort · archived date");
  });

  it("search archive filters by title, client-side", async () => {
    vi.spyOn(api, "listArchivedWorkItems").mockResolvedValue({
      items: [
        wi({ id: "w1", title: "fix the flaky test" }),
        wi({ id: "w2", title: "add retry budget" }),
      ],
      cursor: 1,
    });
    render(
      <MemoryRouter>
        <ArchivedView />
      </MemoryRouter>,
    );
    await userEvent.type(await screen.findByLabelText(/search archive/i), "retry");
    expect(screen.queryByText("fix the flaky test")).toBeNull();
    expect(screen.getByText("add retry budget")).toBeInTheDocument();
  });
});
