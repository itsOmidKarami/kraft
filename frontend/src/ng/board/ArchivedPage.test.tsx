import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { WorkItem } from "../../types";
import { detail, stubFetch } from "../item/testkit";
import { ArchivedPage } from "./ArchivedPage";
import { useBulk } from "./bulk";

const arch = (id: string, title: string, archived_at: string, created_at: string): WorkItem =>
  detail({ id, title, status: "completed", display_status: "archived", archived_at, archived_by: "auto", created_at, bead_id: null });
const A = arch("a1", "Bravo", "2026-09-10T00:00:00Z", "2026-09-01T00:00:00Z");
const B = arch("b2", "alpha", "2026-09-12T00:00:00Z", "2026-08-01T00:00:00Z");

const Where = () => <span data-testid="where">{useLocation().pathname}</span>;
const mount = () =>
  render(
    <MemoryRouter initialEntries={["/archived"]}>
      <Routes>
        <Route path="/archived" element={<ArchivedPage />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
const titles = () => screen.getAllByRole("button", { name: /^(Bravo|alpha)/ }).map((b) => b.textContent!.startsWith("Bravo") ? "Bravo" : "alpha");

let list: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  useBulk.setState({ last: null });
  list = vi.spyOn(api, "listArchivedWorkItems").mockResolvedValue({ items: [A, B], cursor: 1 });
  vi.spyOn(api, "listWorkItems").mockResolvedValue({ items: [], cursor: 1 });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ArchivedPage", () => {
  it("lists the archived items, newest archived first, by Created or Title on request, and filters", async () => {
    mount();
    await act(async () => {});
    expect(list).toHaveBeenCalled();
    expect(titles()).toEqual(["alpha", "Bravo"]);
    expect(screen.getAllByText("archived by auto")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: /Sort/ }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Created" }));
    expect(titles()).toEqual(["Bravo", "alpha"]);
    fireEvent.change(screen.getByRole("searchbox", { name: "Filter" }), { target: { value: "brav" } });
    expect(titles()).toEqual(["Bravo"]);
  });

  it("restores one item from its row, and checked ones in bulk", async () => {
    const calls = stubFetch({ "POST /work-items/bulk": [200, { results: [{ id: "a1", ok: true }, { id: "b2", ok: true }] }] });
    mount();
    await act(async () => {});
    fireEvent.click(screen.getAllByRole("button", { name: "Restore" })[0]);
    await act(async () => {});
    expect(calls.find((c) => c.method === "POST")).toMatchObject({ path: "/work-items/b2/restore" });
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Bravo" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select alpha" }));
    fireEvent.click(screen.getByRole("button", { name: "Restore 2" }));
    await act(async () => {});
    expect(calls.filter((c) => c.path === "/work-items/bulk").at(-1)?.body).toEqual({ action: "restore", ids: ["a1", "b2"] });
  });

  it("opens the item page from a row", async () => {
    mount();
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: /^Bravo/ }));
    expect(screen.getByTestId("where")).toHaveTextContent("/work-items/a1");
  });

  it("says when nothing is archived", async () => {
    list.mockResolvedValue({ items: [], cursor: 1 });
    mount();
    expect(await screen.findByText("Nothing is archived.")).toBeInTheDocument();
  });
});
