import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Chip } from "./Chip";

it.each([
  { name: "a toggle reports its state", props: { pressed: true }, pressed: "true" },
  { name: "an off toggle says so", props: { pressed: false }, pressed: "false" },
  { name: "a sheet opener is not a toggle", props: { set: true }, pressed: null },
])("$name", async ({ props, pressed }) => {
  const onClick = vi.fn();
  render(<Chip onClick={onClick} {...props}>Open</Chip>);
  const b = screen.getByRole("button", { name: "Open" });
  expect(b.getAttribute("aria-pressed")).toBe(pressed);
  await userEvent.click(b);
  expect(onClick).toHaveBeenCalledOnce();
});
