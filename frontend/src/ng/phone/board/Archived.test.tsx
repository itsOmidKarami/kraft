import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mountAt } from "../areas/testkit";
import { Archived } from "./Archived";

const item = (id: string, title: string, archived_at: string | null) => ({ id, title, repo: "/code/kraft", archived_at });
const open = (answer: [number, unknown]) => mountAt(<Archived />, "/archived", "/archived", { "GET /work-items": answer });
afterEach(() => vi.unstubAllGlobals());

describe("Archived (the desktop's /archived at phone width)", () => {
  it("lists the archived items newest first, each opening its item", async () => {
    const { calls } = open([200, { items: [item("w1", "Older", "2026-09-01T08:00:00Z"), item("w2", "Newer", "2026-09-20T08:00:00Z")], cursor: 0 }]);
    expect(screen.getByRole("heading", { level: 1, name: "Archived" })).toBeInTheDocument();
    const newer = await screen.findByRole("link", { name: /Newer/ });
    const links = within(newer.closest(".ph-list") as HTMLElement).getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/work-items/w2", "/work-items/w1"]);
    expect(newer).toHaveTextContent(/kraft · archived \d+d ago/);
    const read = calls.filter((c) => c.method === "GET" && c.path === "/work-items");
    expect(read.map((c) => Object.fromEntries(c.query))).toEqual([{ archived: "true", include_abandoned: "true" }]);
  });

  it("says so when nothing is archived", async () => {
    open([200, { items: [], cursor: 0 }]);
    expect(await screen.findByText("Nothing is archived.")).toBeInTheDocument();
  });

  it("says so when the list can't be read", async () => {
    open([500, { detail: "boom" }]);
    expect(await screen.findByRole("alert")).toHaveTextContent("The archived items could not be read.");
  });
});
