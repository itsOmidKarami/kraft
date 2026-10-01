import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Dialog } from "./Dialog";

describe("ng Dialog", () => {
  it("takes focus itself when it holds nothing focusable, so Escape reaches it and not the pane behind", () => {
    const behind = vi.fn((e: React.KeyboardEvent) => e.stopPropagation());
    const onClose = vi.fn();
    render(<div onKeyDown={behind}><button>Read the spec</button></div>);
    screen.getByRole("button", { name: "Read the spec" }).focus();
    render(<Dialog title="Spec" onClose={onClose}><p>Only text.</p></Dialog>);
    const dialog = screen.getByRole("dialog", { name: "Spec" });
    expect(dialog).toHaveFocus();
    // fireEvent answers false when the key's default (Tab walking out) was prevented.
    expect(fireEvent.keyDown(document.activeElement!, { key: "Tab" })).toBe(false);
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(behind).not.toHaveBeenCalled();
  });

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
