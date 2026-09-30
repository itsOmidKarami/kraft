import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Switch } from "./Switch";

describe("ng Switch", () => {
  it("is a named switch that asks for the opposite state", () => {
    const onChange = vi.fn();
    const { rerender } = render(<Switch label="Wrap long lines" checked={false} onChange={onChange} />);
    const sw = screen.getByRole("switch", { name: "Wrap long lines" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    fireEvent.click(sw);
    expect(onChange).toHaveBeenLastCalledWith(true);
    rerender(<Switch label="Wrap long lines" checked onChange={onChange} />);
    expect(sw).toHaveAttribute("aria-checked", "true");
    fireEvent.click(sw);
    expect(onChange).toHaveBeenLastCalledWith(false);
  });
});
