import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DocViewer, type DocSource } from "./DocViewer";
import { acceptWrites, stubFetch } from "./testkit";

/** The writes these pages send; any other write is refused. */
const WRITES = acceptWrites("POST /documents/d1/open");

afterEach(() => vi.unstubAllGlobals());
const EDITORS = { available: ["code", "zed"], system: true, default: "zed" };
const doc = { id: "d1", title: "Review notes", path: "/code/kraft/.engineering/reviews/r.md", content: "# Review notes\n\nThe cache has **no size bound**.", repo: "/code/kraft" };

function Harness({ source }: { source: DocSource }) {
  const [open, setOpen] = useState(false);
  return <><button onClick={() => setOpen(true)}>open doc</button>{open && <DocViewer source={source} onClose={() => setOpen(false)} />}</>;
}

describe("DocViewer", () => {
  // "System default" goes by name: null asks for the default, which KRAFT_EDITOR can make an editor (#502 review).
  it("opens the default editor from the one button, and the others from its menu", async () => {
    const calls = stubFetch({ ...WRITES, "GET /documents/d1": [200, doc], "GET /editors": [200, EDITORS] });
    render(<Harness source={{ kind: "document", id: "d1" }} />);
    await userEvent.click(screen.getByRole("button", { name: "open doc" }));
    expect(await screen.findByText("no size bound")).toBeInTheDocument();
    const button = await screen.findByRole("button", { name: "Open in editor" });
    await waitFor(() => expect(button).toHaveAttribute("title", "Open in Zed"));
    await userEvent.click(button);
    await userEvent.click(screen.getByRole("button", { name: "Other editors" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Other editors" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["VS Code", "System default"]);
    await userEvent.click(screen.getByRole("menuitem", { name: "System default" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").map((c) => c.body)).toEqual([{ editor: "zed" }, { editor: "system" }]));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("puts the system opener first when no default is chosen, and asks for it by name", async () => {
    const calls = stubFetch({ ...WRITES, "GET /documents/d1": [200, doc], "GET /editors": [200, { ...EDITORS, default: null }] });
    render(<DocViewer source={{ kind: "document", id: "d1" }} onClose={() => {}} />);
    const button = await screen.findByRole("button", { name: "Open in editor" });
    await waitFor(() => expect(button).toHaveAttribute("title", "Open with the system's default app"));
    await userEvent.click(button);
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").map((c) => c.body)).toEqual([{ editor: "system" }]));
    expect(await screen.findByText("Opened in System default.")).toBeInTheDocument();
  });

  it.each([
    ["no editor and no system opener", [200, { available: [], system: false, default: null }], "No editor found on this machine"],
    ["a refusal for a remote client", [403, { detail: "this server only opens editors for a client on its own machine" }], "this server only opens editors for a client on its own machine"],
  ] as const)("disables Open in editor, saying why, with %s", async (_, answer, why) => {
    stubFetch({ "GET /documents/d1": [200, doc], "GET /editors": answer as [number, unknown] });
    render(<DocViewer source={{ kind: "document", id: "d1" }} onClose={() => {}} />);
    await screen.findByText("no size bound");
    await waitFor(() => expect(screen.getByRole("button", { name: "Open in editor" })).toHaveAttribute("title", why));
    expect(screen.getByRole("button", { name: "Open in editor" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Other editors" })).toBeNull();
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
    expect(screen.queryByRole("button", { name: "Open in editor" })).toBeNull();
  });

  it("has a Close button, in the editors' row for an indexed document and alone for an artifact", async () => {
    const onClose = vi.fn();
    stubFetch({ "GET /documents/d1": [200, doc], "GET /editors": [200, EDITORS], "GET /work-items/w1/artifact": [200, { title: "Plan", path: "p.md", content: "# Plan\n\nStep one." }] });
    const { unmount } = render(<DocViewer source={{ kind: "artifact", workItemId: "w1" }} onClose={onClose} />);
    await screen.findByText("Step one.");
    expect(screen.getAllByRole("button").map((b) => b.getAttribute("aria-label") ?? b.textContent)).toEqual(["⤢ full screen", "Close"]);
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    unmount();
    render(<DocViewer source={{ kind: "document", id: "d1" }} onClose={onClose} />);
    await screen.findByText("no size bound");
    await screen.findByRole("button", { name: "Other editors" });
    expect(screen.getAllByRole("button").map((b) => b.getAttribute("aria-label") ?? b.textContent)).toEqual(["⤢ full screen", "Close", "Open in editor", "Other editors", "Copy path"]);
  });

  it("is a drawer whose header stacks the title, the path and who wrote it", async () => {
    stubFetch({ "GET /documents/d1": [200, doc] });
    render(<DocViewer source={{ kind: "document", id: "d1", by: "review › code_review › attempt 2" }} onClose={() => {}} />);
    const drawer = await screen.findByRole("dialog", { name: "Review notes" });
    expect(drawer).toHaveClass("dv-drawer");
    expect([...drawer.querySelectorAll(".dv-title, .dv-path, .dv-by")].map((c) => c.textContent)).toEqual(["Review notes", doc.path, "written by review › code_review › attempt 2"]);
  });

  describe("opened from search", () => {
    const bar = () => screen.getByRole("group", { name: "Search matches" });

    it("counts the query's matches in the text, steps through them with ↑/↓, wrapping, and scrolls the body, not the page", async () => {
      stubFetch({ "GET /documents/d1": [200, doc] });
      // jsdom lays nothing out: each match reads as far down as its text node says.
      Range.prototype.getBoundingClientRect = function (this: Range) { return { top: this.startContainer.nodeValue!.includes("bound") ? 400 : 100 } as DOMRect; };
      const into = vi.fn();
      Element.prototype.scrollIntoView = into;
      render(<DocViewer source={{ kind: "document", id: "d1" }} query="cache bound" onClose={() => {}} />);
      await screen.findByText("no size bound");
      await waitFor(() => expect(bar()).toHaveTextContent("cache bound1 of 2↑↓from search"));
      const body = document.querySelector<HTMLElement>(".dv-body")!;
      expect(body.scrollTop).toBe(100);
      await userEvent.click(screen.getByRole("button", { name: "Next match" }));
      expect(bar()).toHaveTextContent("2 of 2");
      expect(body.scrollTop).toBe(500);
      await userEvent.click(screen.getByRole("button", { name: "Next match" }));
      expect(bar()).toHaveTextContent("1 of 2");
      await userEvent.click(screen.getByRole("button", { name: "Previous match" }));
      expect(bar()).toHaveTextContent("2 of 2");
      expect(into).not.toHaveBeenCalled();
      delete (Range.prototype as Partial<Range>).getBoundingClientRect;
      delete (Element.prototype as Partial<Element>).scrollIntoView;
    });

    it.each([
      ["Review", "matched in the title"],
      ["vector", "no match in the text"],
    ])("says where %s matched when the text has none", async (query, says) => {
      stubFetch({ "GET /documents/d1": [200, { ...doc, content: "Nothing here." }] });
      render(<DocViewer source={{ kind: "document", id: "d1" }} query={query} onClose={() => {}} />);
      await screen.findByText("Nothing here.");
      expect(bar()).toHaveTextContent(says);
      expect(screen.getByRole("button", { name: "Next match" })).toBeDisabled();
    });

    it("has no bar when it was not opened from search", async () => {
      stubFetch({ "GET /documents/d1": [200, doc] });
      render(<DocViewer source={{ kind: "document", id: "d1" }} onClose={() => {}} />);
      await screen.findByText("no size bound");
      expect(screen.queryByRole("group", { name: "Search matches" })).toBeNull();
    });
  });

  it("goes full screen and back, and opens as a drawer again after a close", async () => {
    stubFetch({ "GET /documents/d1": [200, doc] });
    render(<Harness source={{ kind: "document", id: "d1" }} />);
    await userEvent.click(screen.getByRole("button", { name: "open doc" }));
    await userEvent.click(await screen.findByRole("button", { name: "⤢ full screen" }));
    expect(screen.getByRole("dialog")).toHaveClass("is-full");
    await userEvent.click(screen.getByRole("button", { name: "⤡ exit full screen" }));
    expect(screen.getByRole("dialog")).not.toHaveClass("is-full");
    await userEvent.click(screen.getByRole("button", { name: "⤢ full screen" }));
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    await userEvent.click(screen.getByRole("button", { name: "open doc" }));
    expect(await screen.findByRole("dialog")).not.toHaveClass("is-full");
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
