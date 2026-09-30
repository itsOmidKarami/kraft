import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Dialog } from "./Dialog";

describe("ng Dialog", () => {
  it("is a named modal that traps Tab, closes on Escape and gives focus back", () => {
    const opener = document.createElement("button");
    document.body.append(opener);
    opener.focus();
    const onClose = vi.fn();
    const { unmount } = render(
      <Dialog title="Cancel item" onClose={onClose} footer={<button>Confirm</button>}>
        <input aria-label="Reason" />
      </Dialog>,
    );
    expect(screen.getByRole("dialog", { name: "Cancel item" })).toHaveAttribute("aria-modal", "true");
    const reason = screen.getByRole("textbox", { name: "Reason" });
    const confirm = screen.getByRole("button", { name: "Confirm" });
    expect(document.activeElement).toBe(reason);
    confirm.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(reason);
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(confirm);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    unmount();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it("closes on a backdrop press unless dirty", () => {
    const onClose = vi.fn();
    const { rerender } = render(<Dialog title="t" onClose={onClose}>x</Dialog>);
    const backdrop = screen.getByRole("dialog").parentElement!;
    fireEvent.mouseDown(backdrop);
    expect(onClose).toHaveBeenCalledTimes(1);
    rerender(<Dialog title="t" onClose={onClose} dirty>x</Dialog>);
    fireEvent.mouseDown(backdrop);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
