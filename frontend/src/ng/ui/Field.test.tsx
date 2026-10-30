import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Field } from "./Field";

describe("Field", () => {
  it.each([
    { id: "the control says nothing: invalid", own: {}, invalid: "true" },
    { id: "the control owns it: its value stands", own: { "aria-invalid": false }, invalid: "false" },
  ])("an error describes its control, after the control's own description ($id)", ({ own, invalid }) => {
    render(
      <>
        <span id="mine">Own words</span>
        <Field label="Name" error="Taken">
          <input aria-describedby="mine" {...own} />
        </Field>
      </>,
    );
    const box = screen.getByRole("textbox", { name: /^Name/ });
    expect(box).toHaveAccessibleDescription("Own words Taken");
    expect(box).toHaveAttribute("aria-invalid", invalid);
  });
});
