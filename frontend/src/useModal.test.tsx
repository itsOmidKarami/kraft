import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { useModal } from "./useModal";

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
