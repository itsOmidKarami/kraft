import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Snippet } from "./Snippet";

describe("Snippet", () => {
  it("wraps [..] spans in <mark> and leaves the rest as text", () => {
    const { container } = render(<Snippet text="the [reconnect] [backoff] loop" />);
    const marks = container.querySelectorAll("mark");
    expect([...marks].map((m) => m.textContent)).toEqual(["reconnect", "backoff"]);
    expect(container.textContent).toBe("the reconnect backoff loop");
  });

  it("renders plain text when there are no markers", () => {
    render(<Snippet text="nothing to highlight" />);
    expect(screen.getByText("nothing to highlight")).toBeInTheDocument();
  });

  it("treats an unbalanced bracket as literal text", () => {
    const { container } = render(<Snippet text="a [b c" />);
    expect(container.querySelector("mark")).toBeNull();
    expect(container.textContent).toBe("a [b c");
  });
});
