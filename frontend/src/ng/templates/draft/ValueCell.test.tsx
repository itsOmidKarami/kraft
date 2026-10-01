import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ValueCell } from "./ValueCell";

const cell = (onCommit = vi.fn<(t: string) => Promise<string | null> | string | null>(() => null), value = "90") =>
  render(<><ValueCell label="tasks cap" value={value} display={`${value} min`} onCommit={onCommit} /><button type="button">next</button></>);

describe("ValueCell", () => {
  it("is a button until clicked, then an input: Enter commits the typed text", async () => {
    const onCommit = vi.fn(() => null);
    cell(onCommit);
    await userEvent.click(screen.getByRole("button", { name: "tasks cap, 90 min. Edit" }));
    await userEvent.clear(screen.getByRole("textbox", { name: "tasks cap" }));
    await userEvent.type(screen.getByRole("textbox", { name: "tasks cap" }), "60{Enter}");
    expect(onCommit).toHaveBeenCalledWith("60");
    await waitFor(() => expect(screen.getByRole("button", { name: /tasks cap/ })).toBeInTheDocument());
  });

  it("cancels on Escape, sends nothing for an unchanged value, and Escape does not reach the page", async () => {
    const onCommit = vi.fn(() => null);
    const page = vi.fn();
    document.addEventListener("keydown", page);
    cell(onCommit);
    await userEvent.click(screen.getByRole("button", { name: /tasks cap/ }));
    await userEvent.type(screen.getByRole("textbox"), "9{Escape}");
    expect(onCommit).not.toHaveBeenCalled();
    expect(page.mock.calls.some(([e]) => (e as KeyboardEvent).key === "Escape")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: /tasks cap/ }));
    await userEvent.keyboard("{Enter}");
    expect(onCommit).not.toHaveBeenCalled();
    document.removeEventListener("keydown", page);
  });

  it("keeps the input open under a refusal's message and saves nothing", async () => {
    const onCommit = vi.fn(() => Promise.resolve("Refused: above the instance cap"));
    cell(onCommit);
    await userEvent.click(screen.getByRole("button", { name: /tasks cap/ }));
    await userEvent.type(screen.getByRole("textbox"), "9{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Refused: above the instance cap");
    expect(screen.getByRole("textbox", { name: "tasks cap" })).toHaveAttribute("aria-invalid", "true");
  });

  it("commits when focus leaves, and does not take focus back from the cell clicked next", async () => {
    const onCommit = vi.fn(() => null);
    cell(onCommit);
    await userEvent.click(screen.getByRole("button", { name: /tasks cap/ }));
    await userEvent.type(screen.getByRole("textbox"), "5");
    await userEvent.click(screen.getByRole("button", { name: "next" }));
    expect(onCommit).toHaveBeenCalledWith("905");
    await new Promise((r) => requestAnimationFrame(() => r(null)));
    expect(screen.getByRole("button", { name: "next" })).toHaveFocus();
  });
});
