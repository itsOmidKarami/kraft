import { render, waitFor } from "@testing-library/react";
import { Rocket } from "lucide-react";
import { describe, expect, it, vi } from "vitest";
import { NodeIcon } from "./icons";

// The lazy set, as its chunk would answer: one icon beyond the static map.
vi.mock("./iconSetData", () => ({ ICONS: { rocket: Rocket } }));

const svg = (el: React.ReactElement) => render(el).container.querySelector("svg")!;

describe("NodeIcon", () => {
  it("draws the kind's icon until the lazy set brings a name outside the static map", async () => {
    // The set stays cached once any case has loaded it, so this one takes fresh modules.
    vi.resetModules();
    const { NodeIcon: Uncached } = await import("./icons");
    const { container } = render(<Uncached name="rocket" kind="agent" />);
    expect(container.querySelector("svg")).toHaveClass("lucide-sparkles");
    await waitFor(() => expect(container.querySelector("svg")).toHaveClass("lucide-rocket"));
  });
  it("draws a known icon by name", () => {
    expect(svg(<NodeIcon name="shield" kind="agent" />)).toHaveClass("lucide-shield");
  });
  it("falls back to the kind's icon for an unknown name (R32)", async () => {
    const { container } = render(<NodeIcon name="no-such-icon" kind="subprocess" />);
    expect(container.querySelector("svg")).toHaveClass("lucide-terminal");
    // Still the kind's icon once the whole set has loaded.
    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector("svg")).toHaveClass("lucide-terminal");
  });
  it("draws a box for an exec node with no icon", () => {
    expect(svg(<NodeIcon />)).toHaveClass("lucide-box");
  });
});
