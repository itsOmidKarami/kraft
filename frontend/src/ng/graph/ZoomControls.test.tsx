import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ZoomControls } from "./ZoomControls";

describe("ng ZoomControls", () => {
  it("names the Fit button by its visible text and keeps the longer label as its tooltip", () => {
    render(<ZoomControls scale={1} mode="fit" onIn={() => {}} onOut={() => {}} onReset={() => {}} onFit={() => {}} fitLabel="Fit the chain" />);
    expect(screen.getByRole("button", { name: "Fit" })).toHaveAttribute("data-tip", "Fit the chain");
  });
});
