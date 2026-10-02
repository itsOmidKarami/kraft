import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubFetch } from "./testkit";
import { MemoryRouter } from "react-router-dom";
import { Brief, DiffLine, Title } from "./Top";

afterEach(() => vi.unstubAllGlobals());
const writes = (calls: { method: string }[]) => calls.filter((c) => c.method !== "GET");

describe("Title", () => {
  it("renames in place on Enter, restores on Esc, and refuses a blank title", async () => {
    const calls = stubFetch();
    const onSaved = vi.fn();
    render(<Title id="w1" title="Old" onSaved={onSaved} />);
    await userEvent.click(screen.getByRole("button", { name: "Old" }));
    const box = screen.getByRole("textbox", { name: "Title" });
    await userEvent.clear(box);
    await userEvent.type(box, "   {Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("can't be blank");
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("heading", { name: "Old" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Old" }));
    await userEvent.clear(screen.getByRole("textbox", { name: "Title" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Title" }), "New{Enter}");
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(writes(calls)).toEqual([{ method: "PATCH", path: "/work-items/w1", body: { title: "New" } }]);
  });
});

describe("Brief", () => {
  it("edits in the same spot, says who reads it, and saves the description", async () => {
    const calls = stubFetch();
    const onSaved = vi.fn();
    render(<Brief id="w1" brief="Cache embeddings." onSaved={onSaved} />);
    await userEvent.click(screen.getByRole("button", { name: "edit" }));
    expect(screen.getByText("The next agent to launch reads the new brief.")).toBeInTheDocument();
    await userEvent.type(screen.getByRole("textbox", { name: "Brief" }), " Bounded.");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(writes(calls)).toEqual([{ method: "PATCH", path: "/work-items/w1", body: { description: "Cache embeddings. Bounded." } }]);
  });

  it("saves on ⌘↵, a plain ↵ starting a new line", async () => {
    const calls = stubFetch();
    const onSaved = vi.fn();
    render(<Brief id="w1" brief="Cache embeddings." onSaved={onSaved} />);
    await userEvent.click(screen.getByRole("button", { name: "edit" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Brief" }), "{Enter}Bounded.");
    expect(writes(calls)).toEqual([]);
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(writes(calls)).toEqual([{ method: "PATCH", path: "/work-items/w1", body: { description: "Cache embeddings.\nBounded." } }]);
  });

  it("Esc leaves the brief as it was, without a write", async () => {
    const calls = stubFetch();
    render(<Brief id="w1" brief="Cache embeddings." onSaved={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "edit" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Brief" }), " x{Escape}");
    expect(screen.getByText("Cache embeddings.")).toBeInTheDocument();
    expect(writes(calls)).toEqual([]);
  });
});

describe("DiffLine", () => {
  it("counts each path once across landed and in-flight changes, and hides with no diff", async () => {
    stubFetch({ "GET /work-items/w1/diff": [200, { files: [{ path: "a.py", insertions: 2, deletions: 1 }], landed: { commits: [], files: [{ path: "a.py", insertions: 10, deletions: 0 }, { path: "b.py", insertions: 3, deletions: 4 }] } }] });
    const { unmount } = render(<MemoryRouter><DiffLine id="w1" version="v" /></MemoryRouter>);
    expect(await screen.findByText(/2 files/)).toHaveTextContent("2 files +15 −5");
    expect(screen.getByRole("link", { name: "Review changes" })).toHaveAttribute("href", "/work-items/w1/review");
    unmount();
    stubFetch({ "GET /work-items/w1/diff": [200, { files: [] }] });
    const { container } = render(<MemoryRouter><DiffLine id="w1" version="v" /></MemoryRouter>);
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });
});
