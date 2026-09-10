import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import { SearchView } from "./Search";

beforeEach(() => {
  useStore.setState({ workItems: {} } as never);
  vi.spyOn(api, "search").mockResolvedValue({ query: "", mode: "hybrid", results: [] });
  vi.spyOn(api, "searchBeads").mockResolvedValue({ query: "", beads: [] });
});

describe("SearchView", () => {
  it("mounts SearchOverlay embedded, with no dialog role", () => {
    render(
      <MemoryRouter>
        <SearchView />
      </MemoryRouter>,
    );
    expect(screen.getByRole("searchbox")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
