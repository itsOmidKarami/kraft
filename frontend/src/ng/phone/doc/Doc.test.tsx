import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubFetch } from "../../item/testkit";
import { Doc } from "./Doc";

afterEach(() => vi.unstubAllGlobals());
const DOC = { id: "d1", title: "Review notes", path: ".engineering/specs/caching.md", content: "# Review notes\n\nThe cache has no bound.", repo: "/code/kraft" };

describe("phone Doc", () => {
  // The desktop viewer's rule (R13b-02): a scanned file's absolute path; no file, no Copy path.
  it.each([
    ["a scanned file's absolute path", { origin: "git_scan" }, "/code/kraft/.engineering/specs/caching.md"],
    ["the path as it is without an origin", {}, ".engineering/specs/caching.md"],
  ])("copies %s", async (_, over, copied) => {
    stubFetch({ "GET /documents/d1": [200, { ...DOC, ...over }] });
    const writeText = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText } });
    render(<MemoryRouter><Doc id="d1" /></MemoryRouter>);
    await userEvent.click(await screen.findByRole("button", { name: "Copy path" }));
    expect(writeText).toHaveBeenCalledWith(copied);
  });

  it("offers no Copy path for an indexed document with no file", async () => {
    stubFetch({ "GET /documents/d1": [200, { ...DOC, origin: "event_ingest" }] });
    render(<MemoryRouter><Doc id="d1" /></MemoryRouter>);
    await screen.findByText(/The cache has no bound/);
    expect(screen.queryByRole("button", { name: "Copy path" })).toBeNull();
  });
});
