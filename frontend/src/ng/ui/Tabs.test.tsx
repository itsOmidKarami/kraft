import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { Tabs } from "./Tabs";

function Harness() {
  const [v, setV] = useState<"a" | "b" | "c">("b");
  const tabs = [{ value: "a" as const, label: "Overview" }, { value: "b" as const, label: "Log" }, { value: "c" as const, label: "YAML" }];
  return <Tabs id="t" label="Pane" tabs={tabs} value={v} onChange={setV} />;
}

describe("ng Tabs", () => {
  it("keeps one tab stop on the selected tab and moves it with ←/→, Home and End", () => {
    render(<Harness />);
    const tab = (name: string) => screen.getByRole("tab", { name });
    expect(tab("Log")).toHaveAttribute("aria-selected", "true");
    expect(tab("Log")).toHaveAttribute("tabindex", "0");
    expect(tab("Overview")).toHaveAttribute("tabindex", "-1");

    const list = screen.getByRole("tablist");
    fireEvent.keyDown(list, { key: "ArrowRight" });
    expect(tab("YAML")).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(tab("YAML"));
    fireEvent.keyDown(list, { key: "ArrowRight" });
    expect(tab("Overview")).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(list, { key: "ArrowLeft" });
    expect(tab("YAML")).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(list, { key: "Home" });
    expect(tab("Overview")).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(list, { key: "End" });
    expect(document.activeElement).toBe(tab("YAML"));
    fireEvent.click(tab("Log"));
    expect(tab("Log")).toHaveAttribute("aria-selected", "true");
  });
});
