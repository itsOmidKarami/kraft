import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef, useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Popover } from "./Popover";

function Harness({ onClose }: { onClose: () => void }) {
  const anchor = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button ref={anchor}>anchor</button>
      <p>outside</p>
      <Popover anchor={anchor} open onClose={onClose}>
        <button>inside</button>
      </Popover>
    </>
  );
}

describe("ng Popover", () => {
  it("closes on Escape and on a press outside it and its anchor, not on one inside", () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    fireEvent.mouseDown(screen.getByText("inside"));
    fireEvent.mouseDown(screen.getByText("anchor"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(screen.getByText("outside"));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("renders nothing while closed", () => {
    const anchor = { current: null };
    render(<Popover anchor={anchor} open={false} onClose={() => {}}>x</Popover>);
    expect(screen.queryByText("x")).toBeNull();
  });

  it("opens under its anchor, or above it when there is no room below", () => {
    const place = (anchorTop: number) => {
      document.body.innerHTML = "";
      const anchor = document.createElement("button");
      document.body.appendChild(anchor);
      vi.spyOn(anchor, "getBoundingClientRect").mockReturnValue({ top: anchorTop, bottom: anchorTop + 20, left: 10, right: 60, width: 50, height: 20, x: 10, y: anchorTop, toJSON: () => ({}) });
      vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(200);
      vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(100);
      const { unmount } = render(<Popover anchor={{ current: anchor }} open onClose={() => {}}><p>card</p></Popover>);
      const top = (screen.getByText("card").parentElement as HTMLElement).style.top;
      unmount();
      vi.restoreAllMocks();
      return top;
    };
    Object.defineProperty(window, "innerHeight", { value: 800, configurable: true });
    expect(place(100)).toBe("124px");
    expect(place(760)).toBe("556px");
  });

  describe("from the keyboard", () => {
    /** A trigger that opens a menu of three items, the middle one disabled, as Toolbar's Pop and Composer's split do. */
    function Opener({ focusIn }: { focusIn?: boolean }) {
      const anchor = useRef<HTMLButtonElement>(null);
      const [open, setOpen] = useState(false);
      return (
        <>
          <button ref={anchor} onClick={() => setOpen(true)}>trigger</button>
          <Popover anchor={anchor} open={open} onClose={() => setOpen(false)} role="menu" label="Things" focusIn={focusIn}>
            <span className="menu-heading">Things</span>
            <button role="menuitemradio" aria-checked>one</button>
            <button role="menuitemcheckbox" aria-checked={false} disabled>two</button>
            <button role="menuitemcheckbox" aria-checked={false}>three</button>
          </Popover>
        </>
      );
    }
    const openIt = (focusIn?: boolean) => {
      render(<Opener focusIn={focusIn} />);
      const trigger = screen.getByRole("button", { name: "trigger" });
      trigger.focus();
      fireEvent.click(trigger);
      return trigger;
    };

    it("moves focus to the first live item once it is placed", async () => {
      openIt();
      await waitFor(() => expect(screen.getByRole("menuitemradio", { name: "one" })).toHaveFocus());
    });

    it("moves between the live items with the arrows, Home and End, skipping a disabled one", async () => {
      openIt();
      const one = screen.getByRole("menuitemradio", { name: "one" });
      const three = screen.getByRole("menuitemcheckbox", { name: "three" });
      await waitFor(() => expect(one).toHaveFocus());
      fireEvent.keyDown(one, { key: "ArrowDown" });
      expect(three).toHaveFocus();
      fireEvent.keyDown(three, { key: "ArrowDown" });
      expect(one).toHaveFocus();
      fireEvent.keyDown(one, { key: "ArrowUp" });
      expect(three).toHaveFocus();
      fireEvent.keyDown(three, { key: "Home" });
      expect(one).toHaveFocus();
      fireEvent.keyDown(one, { key: "End" });
      expect(three).toHaveFocus();
    });

    it("hands focus back to what opened it on Escape", async () => {
      const trigger = openIt();
      await waitFor(() => expect(screen.getByRole("menuitemradio", { name: "one" })).toHaveFocus());
      fireEvent.keyDown(document.activeElement!, { key: "Escape" });
      expect(screen.queryByRole("menu")).toBeNull();
      expect(trigger).toHaveFocus();
    });

    it("closes a menu on Tab and hands focus back, rather than leaving it open with focus at the top of the page", async () => {
      const trigger = openIt();
      await waitFor(() => expect(screen.getByRole("menuitemradio", { name: "one" })).toHaveFocus());
      fireEvent.keyDown(document.activeElement!, { key: "Tab" });
      expect(screen.queryByRole("menu")).toBeNull();
      expect(trigger).toHaveFocus();
    });

    it("leaves focus where it is with focusIn off (a toggle that opens on hover or focus)", async () => {
      const trigger = openIt(false);
      await new Promise((r) => requestAnimationFrame(() => r(null)));
      expect(screen.getByRole("menu")).toBeInTheDocument();
      expect(trigger).toHaveFocus();
    });

    it("keeps Tab inside a dialog, cycling last to first and back, with the dialog still open", async () => {
      function Card() {
        const anchor = useRef<HTMLButtonElement>(null);
        const [open, setOpen] = useState(false);
        return (
          <>
            <button ref={anchor} onClick={() => setOpen(true)}>trigger</button>
            <Popover anchor={anchor} open={open} onClose={() => setOpen(false)} role="dialog" label="Cancel this item?">
              <textarea aria-label="Reason" />
              <button disabled>Cancel item</button>
              <button>Keep it going</button>
            </Popover>
          </>
        );
      }
      render(<Card />);
      fireEvent.click(screen.getByRole("button", { name: "trigger" }));
      const reason = screen.getByRole("textbox", { name: "Reason" });
      const keep = screen.getByRole("button", { name: "Keep it going" });
      await waitFor(() => expect(reason).toHaveFocus());
      keep.focus();
      fireEvent.keyDown(keep, { key: "Tab" });
      expect(reason).toHaveFocus();
      fireEvent.keyDown(reason, { key: "Tab", shiftKey: true });
      expect(keep).toHaveFocus();
      expect(screen.getByRole("dialog", { name: "Cancel this item?" })).toBeInTheDocument();
    });

    it("leaves a Tab between a dialog's own fields to the browser", async () => {
      render(<><button>before</button><Popover anchor={{ current: null }} open onClose={() => {}} role="dialog" label="d"><button>a</button><button>b</button></Popover></>);
      const a = screen.getByRole("button", { name: "a" });
      a.focus();
      expect(fireEvent.keyDown(a, { key: "Tab" })).toBe(true);
    });
  });
});
