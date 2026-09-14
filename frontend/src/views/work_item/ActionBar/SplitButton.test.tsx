import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SplitButton } from "./SplitButton";

describe("SplitButton", () => {
  it("primary click calls onPrimary, not onMenuSelect", async () => {
    const onPrimary = vi.fn();
    const onMenuSelect = vi.fn();
    render(
      <SplitButton
        primaryLabel="Escalate"
        onPrimary={onPrimary}
        menuLabel="Escalate in new thread"
        menuHint="Starts without turns 1–3."
        onMenuSelect={onMenuSelect}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Escalate" }));
    expect(onPrimary).toHaveBeenCalledOnce();
    expect(onMenuSelect).not.toHaveBeenCalled();
  });

  it("caret opens a one-entry menu; picking it calls onMenuSelect", async () => {
    const onPrimary = vi.fn();
    const onMenuSelect = vi.fn();
    render(
      <SplitButton
        primaryLabel="Escalate"
        onPrimary={onPrimary}
        menuLabel="Escalate in new thread"
        menuHint="Starts without turns 1–3."
        onMenuSelect={onMenuSelect}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Escalate options" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Escalate in new thread" }));
    expect(onMenuSelect).toHaveBeenCalledOnce();
    expect(onPrimary).not.toHaveBeenCalled();
  });

  it("disabled disables the primary button", () => {
    render(
      <SplitButton
        primaryLabel="Escalate"
        onPrimary={() => {}}
        disabled
        menuLabel="x"
        menuHint="y"
        onMenuSelect={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "Escalate" })).toBeDisabled();
  });
});
