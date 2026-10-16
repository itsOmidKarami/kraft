import { readFileSync } from "node:fs";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Field } from "./Field";
import { LinkButton } from "./LinkButton";
import { Select } from "./Select";

describe("ng LinkButton", () => {
  it("is disabled with its reason as the accessible description, and clicks when it works", () => {
    const onClick = vi.fn();
    const { rerender } = render(<LinkButton reason="Nothing to clear" onClick={onClick}>Clear</LinkButton>);
    const btn = screen.getByRole("button", { name: "Clear" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAccessibleDescription("Nothing to clear");
    rerender(<LinkButton reason={null} onClick={onClick}>Clear</LinkButton>);
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Clear" })).not.toHaveAttribute("aria-describedby");
  });

  it("renders an anchor when given an href", () => {
    render(<LinkButton href="/log.txt" inline>Download</LinkButton>);
    expect(screen.getByRole("link", { name: "Download" })).toHaveClass("link-btn", "is-inline");
  });
});

describe("ng LinkButton in a sentence", () => {
  it("takes its sentence's size, inline or inside an Empty", () => {
    const css = readFileSync("src/ng/ui/ui.css", "utf-8");
    const rule = css.match(/^([^{\n]*\.link-btn[^{\n]*)\{[^}]*font-size:\s*inherit/m)?.[1] ?? "";
    expect(rule).toContain(".link-btn.is-inline");
    expect(rule).toContain(".empty .link-btn");
  });
});

describe("ng Select", () => {
  it("is a native select named by its Field label and passes the choice through", () => {
    const onChange = vi.fn();
    render(<Field label="Chain"><Select defaultValue="a" onChange={(e) => onChange(e.target.value)}><option value="a">A</option><option value="b">B</option></Select></Field>);
    const sel = screen.getByRole("combobox", { name: "Chain" });
    expect(sel.tagName).toBe("SELECT");
    fireEvent.change(sel, { target: { value: "b" } });
    expect(onChange).toHaveBeenCalledWith("b");
  });
});
