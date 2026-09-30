import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { Theme } from "../../types";
import { AppearancePage } from "./AppearancePage";

const BASE: Theme = {
  palette: "nocturne",
  mode: "dark",
  density: "compact",
  board: { group_by: "status", show_done: 5, open_in: "peek" },
  surface: "slate",
  accent: "blue",
  colour_amount: "subtle",
  derived: false,
};

function setup(theme: Partial<Theme> = {}) {
  const loaded = { ...BASE, ...theme };
  vi.spyOn(api, "getTheme").mockResolvedValue(loaded);
  // As the server does: merge into the file, answer with the whole of it.
  let server = loaded;
  const put = vi.spyOn(api, "putTheme").mockImplementation(async (body) => (server = { ...server, ...body, derived: false }));
  render(<AppearancePage />);
  return put;
}
const pressed = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name}`) }).getAttribute("aria-pressed");

afterEach(() => vi.restoreAllMocks());

describe("ng AppearancePage", () => {
  it("sends only the key each control changes", async () => {
    const put = setup();
    await screen.findByRole("radiogroup", { name: "Mode" });
    fireEvent.click(screen.getByRole("radio", { name: "Light" }));
    expect(screen.getByText("Slate · blue accent · light · subtle")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Full" }));
    fireEvent.click(screen.getByRole("button", { name: /^Moss/ }));
    fireEvent.click(screen.getByRole("button", { name: "Rose" }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(4));
    expect(put.mock.calls.map((c) => c[0])).toEqual([{ mode: "light" }, { colour_amount: "full" }, { surface: "moss" }, { accent: "rose" }]);
    expect(await screen.findByText("Moss · rose accent · light · full")).toBeInTheDocument();
    expect(document.documentElement.dataset).toMatchObject({ surface: "moss", accent: "rose", amount: "full", mode: "light" });
  });

  it("paints a change before the server answers", async () => {
    setup();
    vi.mocked(api.putTheme).mockReturnValueOnce(new Promise(() => {}));
    fireEvent.click(await screen.findByRole("button", { name: /^Ink/ }));
    expect(document.documentElement.dataset.surface).toBe("ink");
  });

  it("Mono clears the accent and locks the accent choices", async () => {
    const put = setup();
    fireEvent.click(await screen.findByRole("radio", { name: "Mono" }));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ colour_amount: "mono", accent: "none" }));
    expect(screen.getByRole("button", { name: "Blue" })).toBeDisabled();
    expect(pressed("None")).toBe("true");
    expect(screen.getByText(/Mono has no accent/)).toBeInTheDocument();
  });

  it("shows derived values selected, and writes all three on the first change", async () => {
    const put = setup({ palette: "forest", surface: "moss", accent: "green", colour_amount: "full", derived: true });
    await screen.findByText(/follow the forest palette/);
    expect(pressed("Moss")).toBe("true");
    expect(pressed("Green")).toBe("true");
    expect(screen.getByRole("radio", { name: "Full" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("button", { name: /^Sand/ }));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ surface: "sand", accent: "green", colour_amount: "full" }));
    expect(screen.queryByText(/follow the forest palette/)).toBeNull();
  });

  it("puts the page back and says so when a save fails", async () => {
    setup();
    vi.mocked(api.putTheme).mockRejectedValueOnce(new Error("422: nope"));
    fireEvent.click(await screen.findByRole("button", { name: /^Moss/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: 422: nope");
    expect(pressed("Slate")).toBe("true");
    expect(document.documentElement.dataset.surface).toBe("slate");
  });
});
