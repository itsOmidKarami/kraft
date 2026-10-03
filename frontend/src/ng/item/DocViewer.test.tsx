import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DocViewer, EDITORS, type DocSource } from "./DocViewer";
import { acceptWrites, stubFetch } from "./testkit";

/** The writes these pages send; any other write is refused. */
const WRITES = acceptWrites("POST /documents/d1/open");

afterEach(() => vi.unstubAllGlobals());
const doc = { id: "d1", title: "Review notes", path: "/code/kraft/.engineering/reviews/r.md", content: "# Review notes\n\nThe cache has **no size bound**.", repo: "/code/kraft" };

function Harness({ source }: { source: DocSource }) {
  const [open, setOpen] = useState(false);
  return <><button onClick={() => setOpen(true)}>open doc</button>{open && <DocViewer source={source} onClose={() => setOpen(false)} />}</>;
}

describe("DocViewer", () => {
  it.each(EDITORS)("opens an indexed document in $name", async (e) => {
    const calls = stubFetch({ ...WRITES, "GET /documents/d1": [200, doc] });
    render(<DocViewer source={{ kind: "document", id: "d1" }} onClose={() => {}} />);
    expect(await screen.findByText("no size bound")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: e.name }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/documents/d1/open", body: { editor: e.id } }]));
  });

  it("copies the path", async () => {
    stubFetch({ "GET /documents/d1": [200, doc] });
    const writeText = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText } });
    render(<DocViewer source={{ kind: "document", id: "d1" }} onClose={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Copy path" }));
    expect(writeText).toHaveBeenCalledWith(doc.path);
  });

  it("shows a gate's artifact without editors or a path to copy (it has no index row)", async () => {
    stubFetch({ "GET /work-items/w1/artifact": [200, { title: "Plan", path: ".engineering/plans/p.md", content: "# Plan\n\nStep one." }] });
    render(<DocViewer source={{ kind: "artifact", workItemId: "w1" }} onClose={() => {}} />);
    expect(await screen.findByText("Step one.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy path" })).toBeNull();
    expect(screen.queryByRole("button", { name: "VS Code" })).toBeNull();
  });

  it("has a Close button, in the editors' row for an indexed document and alone for an artifact", async () => {
    stubFetch({ "GET /documents/d1": [200, doc], "GET /work-items/w1/artifact": [200, { title: "Plan", path: "p.md", content: "# Plan\n\nStep one." }] });
    const onClose = vi.fn();
    const { unmount } = render(<DocViewer source={{ kind: "artifact", workItemId: "w1" }} onClose={onClose} />);
    await screen.findByText("Step one.");
    expect(screen.getAllByRole("button").map((b) => b.getAttribute("aria-label") ?? b.textContent)).toEqual(["Close"]);
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    unmount();
    render(<DocViewer source={{ kind: "document", id: "d1" }} onClose={onClose} />);
    await screen.findByText("no size bound");
    expect(screen.getAllByRole("button").map((b) => b.getAttribute("aria-label") ?? b.textContent)).toEqual([...EDITORS.map((e) => e.name), "Copy path", "Close"]);
  });

  it("is a drawer whose header stacks the title, the path and who wrote it", async () => {
    stubFetch({ "GET /documents/d1": [200, doc] });
    render(<DocViewer source={{ kind: "document", id: "d1", by: "review › code_review › attempt 2" }} onClose={() => {}} />);
    const drawer = await screen.findByRole("dialog", { name: "Review notes" });
    expect(drawer).toHaveClass("dv-drawer");
    expect([...drawer.querySelector(".dv-head-text")!.children].map((c) => c.textContent)).toEqual(["Review notes", doc.path, "written by review › code_review › attempt 2"]);
  });

  it("closes on a press on the scrim, not on one inside the drawer", async () => {
    stubFetch({ "GET /documents/d1": [200, doc] });
    const onClose = vi.fn();
    render(<DocViewer source={{ kind: "document", id: "d1" }} onClose={onClose} />);
    await userEvent.click(await screen.findByText("no size bound"));
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(document.querySelector(".dv-scrim")!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape and hands focus back", async () => {
    stubFetch({ "GET /documents/d1": [200, doc] });
    render(<Harness source={{ kind: "document", id: "d1" }} />);
    const opener = screen.getByRole("button", { name: "open doc" });
    await userEvent.click(opener);
    await screen.findByText("no size bound");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
  });
});
