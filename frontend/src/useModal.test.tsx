import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { backdropProps, useModal } from "./useModal";

function Dialog({ onClose }: { onClose: () => void }) {
  const ref = useModal<HTMLDivElement>(onClose);
  return (
    <div>
      <button>outside</button>
      <div role="dialog" aria-modal="true" aria-label="test" ref={ref}>
        <button>first</button>
        <button>last</button>
      </div>
    </div>
  );
}

describe("useModal", () => {
  it("focuses the first control on open", () => {
    render(<Dialog onClose={() => {}} />);
    expect(screen.getByRole("button", { name: "first" })).toHaveFocus();
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    render(<Dialog onClose={onClose} />);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("keeps Tab inside the dialog", async () => {
    render(<Dialog onClose={() => {}} />);
    const first = screen.getByRole("button", { name: "first" });
    const last = screen.getByRole("button", { name: "last" });

    await userEvent.tab();
    expect(last).toHaveFocus();
    // wrapping forward from the last control returns to the first, rather than
    // escaping to the "outside" button behind the dialog
    await userEvent.tab();
    expect(first).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(last).toHaveFocus();
  });

  it("restores focus to the opener on unmount", () => {
    render(
      <>
        <button>opener</button>
        <div />
      </>,
    );
    const opener = screen.getByRole("button", { name: "opener" });
    opener.focus();
    const { unmount } = render(<Dialog onClose={() => {}} />);
    expect(screen.getByRole("button", { name: "first" })).toHaveFocus();
    unmount();
    expect(opener).toHaveFocus();
  });
});

function Backdrop({ onClose }: { onClose: () => void }) {
  return (
    <div data-testid="backdrop" {...backdropProps(onClose)}>
      <div className="dialog">
        <button>inside</button>
      </div>
    </div>
  );
}

describe("backdropProps", () => {
  it("closes on a press on the backdrop itself", async () => {
    const onClose = vi.fn();
    render(<Backdrop onClose={onClose} />);
    await userEvent.click(screen.getByTestId("backdrop"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("ignores a press that started inside the dialog", async () => {
    const onClose = vi.fn();
    render(<Backdrop onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "inside" }));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("ignores a non-primary button", () => {
    const onClose = vi.fn();
    render(<Backdrop onClose={onClose} />);
    fireEvent.mouseDown(screen.getByTestId("backdrop"), { button: 2 });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("ignores a press on the backdrop's scrollbar", () => {
    const onClose = vi.fn();
    render(<Backdrop onClose={onClose} />);
    // jsdom lays nothing out, so clientWidth is 0: any positive offset stands
    // in for the strip of backdrop past its own scrollbar
    const press = new MouseEvent("mousedown", { bubbles: true, button: 0 });
    // fireEvent cannot pass offsetX: jsdom exposes it as a getter
    Object.defineProperty(press, "offsetX", { value: 8 });
    fireEvent(screen.getByTestId("backdrop"), press);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("ignores a drag released over the backdrop", async () => {
    const onClose = vi.fn();
    render(<Backdrop onClose={onClose} />);
    const backdrop = screen.getByTestId("backdrop");
    // text selected inside the dialog and released outside it: mousedown lands
    // on the dialog, so only the bubbled click reaches the backdrop
    await userEvent.pointer([
      { keys: "[MouseLeft>]", target: screen.getByRole("button", { name: "inside" }) },
      { target: backdrop },
      { keys: "[/MouseLeft]" },
    ]);
    expect(onClose).not.toHaveBeenCalled();
  });
});
