import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as sheets from "./Sheet";
import { ChoiceSheet, ConfirmSheet, EditSheet, useSheet } from "./Sheet";

afterEach(() => window.history.replaceState(null, "", "/"));

describe("the three shapes (A.5)", () => {
  it("exports exactly a confirm, a choice and an edit, so a fourth shape is a deliberate change", () => {
    expect(Object.keys(sheets).sort()).toEqual(["ChoiceSheet", "ConfirmSheet", "EditSheet", "useSheet"]);
  });

  it("has at most one primary action in each", () => {
    const a = render(<ConfirmSheet title="Pause?" confirm={{ label: "Pause now", run: () => {} }} onClose={() => {}} />);
    expect(document.querySelectorAll(".ph-btn-primary")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    a.unmount();
    const b = render(<ChoiceSheet title="Repo" options={[{ value: "a", label: "A" }, { value: "b", label: "B" }]} value="b" onPick={() => {}} onClose={() => {}} />);
    expect(document.querySelectorAll(".ph-btn-primary")).toHaveLength(0);
    expect(screen.getByRole("radio", { name: "B", checked: true })).toBeInTheDocument();
    b.unmount();
    render(<EditSheet title="Port" onSubmit={() => {}} onClose={() => {}} />);
    expect(document.querySelectorAll(".ph-btn-primary")).toHaveLength(1);
  });

  it("is a modal dialog named by its title, with the first field focused", () => {
    render(<EditSheet title="Port" initial="8765" onSubmit={() => {}} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Port" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByLabelText("Port", { selector: "input" })).toHaveFocus();
  });

  it("keeps a failure inside the sheet and the sheet open", async () => {
    const onSubmit = vi.fn();
    render(<EditSheet title="Port" error="port is in use" onSubmit={onSubmit} onClose={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("port is in use");
    await userEvent.type(screen.getByLabelText("Port", { selector: "input" }), "9{Enter}");
    expect(onSubmit).toHaveBeenCalledWith("9");
  });

  it("closes on Escape, the scrim and Cancel through one path", async () => {
    const onClose = vi.fn();
    const { container } = render(<ConfirmSheet title="Pause?" confirm={{ label: "Pause", run: () => {} }} onClose={onClose} />);
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.click(document.querySelector(".ph-scrim")!);
    expect(onClose).toHaveBeenCalledTimes(3);
    expect(container).toBeTruthy();
  });

  it("returns focus to the opener on close", async () => {
    function Screen() {
      const sheet = useSheet();
      return (
        <>
          <button type="button" onClick={() => sheet.open("pause")}>open</button>
          {sheet.is("pause") && <ConfirmSheet title="Pause?" confirm={{ label: "Pause", run: sheet.close }} onClose={sheet.close} />}
        </>
      );
    }
    render(<BrowserRouter><Routes><Route path="*" element={<Screen />} /></Routes></BrowserRouter>);
    const opener = screen.getByRole("button", { name: "open" });
    opener.focus();
    await userEvent.click(opener);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(opener).toHaveFocus();
  });
});

describe("a sheet is a history entry", () => {
  function Screen() {
    const sheet = useSheet();
    const { pathname } = useLocation();
    return (
      <>
        <output aria-label="where">{pathname}</output>
        <button type="button" onClick={() => sheet.open("pause")}>open</button>
        {sheet.is("pause") && <ConfirmSheet title="Pause?" confirm={{ label: "Pause", run: () => sheet.goTo("/elsewhere") }} onClose={sheet.close} />}
      </>
    );
  }
  const mount = () => render(<BrowserRouter><Routes><Route path="*" element={<Screen />} /></Routes></BrowserRouter>);

  it("closes the sheet, not the screen, when the browser goes Back", async () => {
    window.history.replaceState(null, "", "/work-items/a");
    mount();
    await userEvent.click(screen.getByRole("button", { name: "open" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    window.history.back();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByLabelText("where")).toHaveTextContent("/work-items/a");
  });

  it("ignores a close that arrives after the sheet is already closed, so a late confirm cannot pop the screen under it", async () => {
    window.history.replaceState(null, "", "/");
    window.history.pushState(null, "", "/work-items/a");
    let close = () => {};
    function Late() {
      const sheet = useSheet();
      close = sheet.close;
      return <><output aria-label="where">{useLocation().pathname}</output><button type="button" onClick={() => sheet.open("pause")}>open</button>{sheet.is("pause") && <ConfirmSheet title="Pause?" confirm={{ label: "Pause", run: () => {} }} onClose={sheet.close} />}</>;
    }
    render(<BrowserRouter><Routes><Route path="*" element={<Late />} /></Routes></BrowserRouter>);
    await userEvent.click(screen.getByRole("button", { name: "open" }));
    const lateClose = close;
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    lateClose();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByLabelText("where")).toHaveTextContent("/work-items/a");
  });

  it("leaves the sheet's entry behind when an action goes elsewhere, so Back reaches the screen under it", async () => {
    window.history.replaceState(null, "", "/work-items/a");
    mount();
    const idx = () => (window.history.state as { idx: number }).idx;
    const base = idx();
    await userEvent.click(screen.getByRole("button", { name: "open" }));
    expect(idx()).toBe(base + 1);
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    await waitFor(() => expect(screen.getByLabelText("where")).toHaveTextContent("/elsewhere"));
    expect(idx()).toBe(base + 1);
  });
});
