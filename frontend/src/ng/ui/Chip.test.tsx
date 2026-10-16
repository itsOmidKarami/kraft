import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Chip } from "./Chip";

describe("Chip", () => {
  it("is a toggle button that reports its state", () => {
    const onClick = vi.fn();
    const { rerender } = render(<Chip pressed={false} onClick={onClick}>high</Chip>);
    const chip = screen.getByRole("button", { name: "high" });
    expect(chip).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(chip);
    expect(onClick).toHaveBeenCalledOnce();
    rerender(<Chip pressed onClick={onClick}>high</Chip>);
    expect(chip).toHaveAttribute("aria-pressed", "true");
  });

  it("reports a radio option as checked, not pressed", () => {
    render(<Chip role="radio" pressed>Nit</Chip>);
    const radio = screen.getByRole("radio", { name: "Nit" });
    expect(radio).toHaveAttribute("aria-checked", "true");
    expect(radio).not.toHaveAttribute("aria-pressed");
  });

  it("says nothing of state when it is only a button", () => {
    render(<Chip>repo</Chip>);
    expect(screen.getByRole("button", { name: "repo" })).not.toHaveAttribute("aria-pressed");
  });
});
