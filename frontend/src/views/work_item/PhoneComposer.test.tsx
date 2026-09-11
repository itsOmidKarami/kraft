import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PhoneComposer } from "./PhoneComposer";

describe("PhoneComposer", () => {
  it("renders Cancel, the title and the context line", () => {
    render(
      <PhoneComposer title="Steer" context="w1 · verify" onCancel={() => {}}>
        <p>body</p>
      </PhoneComposer>,
    );
    expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument();
    expect(screen.getByText("Steer")).toBeInTheDocument();
    expect(screen.getByText("w1 · verify")).toBeInTheDocument();
  });
});
