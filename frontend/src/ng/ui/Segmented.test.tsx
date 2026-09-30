import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { Segmented } from "./Segmented";

function Harness() {
  const [v, setV] = useState("dark");
  const opts = [{ value: "light", label: "Light" }, { value: "dark", label: "Dark" }, { value: "system", label: "System" }];
  return <Segmented label="Mode" options={opts} value={v} onChange={setV} />;
}

describe("ng Segmented", () => {
  it("is a radio group with one tab stop; arrows, Home and End change the choice", () => {
    render(<Harness />);
    const radio = (name: string) => screen.getByRole("radio", { name });
    expect(screen.getByRole("radiogroup", { name: "Mode" })).toBeInTheDocument();
    expect(radio("Dark")).toHaveAttribute("aria-checked", "true");
    expect(radio("Light")).toHaveAttribute("tabindex", "-1");

    const group = screen.getByRole("radiogroup");
    fireEvent.keyDown(group, { key: "ArrowRight" });
    expect(radio("System")).toHaveAttribute("aria-checked", "true");
    expect(document.activeElement).toBe(radio("System"));
    fireEvent.keyDown(group, { key: "ArrowRight" });
    expect(radio("Light")).toHaveAttribute("aria-checked", "true");
    fireEvent.keyDown(group, { key: "End" });
    expect(radio("System")).toHaveAttribute("aria-checked", "true");
    fireEvent.click(radio("Dark"));
    expect(radio("Dark")).toHaveAttribute("aria-checked", "true");
  });
});
