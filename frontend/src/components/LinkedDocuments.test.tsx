import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { LinkedDocuments } from "./LinkedDocuments";

const doc = {
  document_id: "d1",
  repo: "/r",
  title: "Implementation session",
  kind: "sessions",
  source_kind: "session_summary",
  path: ".engineering/sessions/s1.md",
  node_id: "implementation",
  hook_point: "on.implementation.start",
  worker_session_id: "s1",
  attachment_kind: null,
};

const wrap = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>);

afterEach(() => vi.restoreAllMocks());

describe("LinkedDocuments", () => {
  it("renders the documents linked to the work item", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [doc],
    });
    wrap(<LinkedDocuments workItemId="w1" eventCount={0} />);
    expect(await screen.findByText("Implementation session")).toBeInTheDocument();
    expect(screen.getByText(".engineering/sessions/s1.md")).toBeInTheDocument();
    expect(screen.getByText("sessions")).toBeInTheDocument();
    expect(screen.getByText("implementation")).toBeInTheDocument();
    expect(screen.queryByText("attached at intake")).not.toBeInTheDocument();
  });

  it("tags a document attached at intake", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [{ ...doc, attachment_kind: "plan" }],
    });
    wrap(<LinkedDocuments workItemId="w1" eventCount={0} />);
    expect(await screen.findByText("attached at intake")).toBeInTheDocument();
  });

  it("shows a quiet empty state when nothing is linked yet", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [],
    });
    wrap(<LinkedDocuments workItemId="w1" eventCount={0} />);
    expect(await screen.findByText(/no linked documents/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("surfaces a fetch failure without breaking the view", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockRejectedValue(new Error("boom"));
    wrap(<LinkedDocuments workItemId="w1" eventCount={0} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("opens the document viewer when a row is clicked", async () => {
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [doc],
    });
    const getDocument = vi.spyOn(api, "getDocument").mockResolvedValue({
      id: "d1",
      repo: "/r",
      source_kind: "session_summary",
      kind: "sessions",
      title: "Implementation session",
      path: ".engineering/sessions/s1.md",
      content: "rewrote the adapter\n",
      metadata: {},
      source_created_at: null,
      source_updated_at: null,
      indexed_at: "t",
      links: [],
    });
    wrap(<LinkedDocuments workItemId="w1" eventCount={0} />);
    await userEvent.click(await screen.findByRole("button", { name: /Implementation session/ }));
    await waitFor(() => expect(getDocument).toHaveBeenCalledWith("d1"));
    expect(await screen.findByText(/rewrote the adapter/)).toBeInTheDocument();
  });

  it("refetches when the item's event count changes", async () => {
    const fetchDocs = vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      work_item_id: "w1",
      documents: [],
    });
    const { rerender } = wrap(<LinkedDocuments workItemId="w1" eventCount={0} />);
    await waitFor(() => expect(fetchDocs).toHaveBeenCalledTimes(1));
    rerender(
      <MemoryRouter>
        <LinkedDocuments workItemId="w1" eventCount={1} />
      </MemoryRouter>,
    );
    await waitFor(() => expect(fetchDocs).toHaveBeenCalledTimes(2));
  });
});
