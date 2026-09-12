import { fireEvent, render } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { GraphSplit } from "./GraphSplit";

const graphDiv = () => document.querySelector(".graph-split-graph") as HTMLElement;

describe("GraphSplit", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  // Kraft-6d40: the stored/dragged value must be a ceiling on the graph's
  // own content, not a fixed reservation -- a one-row chain must not carry
  // whatever height an eleven-node chain was dragged to. `height` forces a
  // reservation regardless of content; `maxHeight` lets the div size to its
  // content and only clamps once content exceeds it.
  it("clamps with maxHeight, not a fixed height, so a one-row graph doesn't reserve unused space", () => {
    render(<GraphSplit graph={<div>one row</div>} lower={<div>lower</div>} />);
    expect(graphDiv().style.height).toBe("");
    expect(graphDiv().style.maxHeight).toBe("96px");
  });

  it("reads a persisted height as the max, clamped to [40, 420]", () => {
    localStorage.setItem("kraft.item.graph_height", "9999");
    render(<GraphSplit graph={<div />} lower={<div />} />);
    expect(graphDiv().style.maxHeight).toBe("420px");
  });

  it("dragging the handle updates and persists the max height", () => {
    render(<GraphSplit graph={<div />} lower={<div />} />);
    const handle = document.querySelector(".graph-split-handle") as HTMLElement;
    fireEvent.mouseDown(handle, { clientY: 100 });
    fireEvent.mouseMove(document, { clientY: 140 });
    expect(graphDiv().style.maxHeight).toBe("136px");
    fireEvent.mouseUp(document);
    expect(localStorage.getItem("kraft.item.graph_height")).toBe("136");
  });
});
