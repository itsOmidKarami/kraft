import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { Theme } from "../../types";
import { AppearancePage } from "./AppearancePage";
import { WithHeader } from "./testkit";

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
  render(<WithHeader><AppearancePage /></WithHeader>);
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

  it("puts the look first, then Density, and none of the code or board choices", async () => {
    setup();
    await screen.findByRole("radiogroup", { name: "Mode" });
    expect(screen.getByText("Saved on change")).toHaveClass("saved-note");
    const order = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(order).toEqual(["Mode", "Colour amount", "Surface", "Accent", "Density", "Preview"]);
    for (const moved of ["Open items in", "Default editor", "Wrap long lines"]) expect(screen.queryByText(moved)).toBeNull();
  });

  it("saves density alone", async () => {
    const put = setup();
    fireEvent.click(await screen.findByRole("radio", { name: "Comfortable" }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    expect(put.mock.calls[0][0]).toEqual({ density: "comfortable" });
  });

  it("names the preview's item by an id in the board's shortened form", async () => {
    setup();
    await screen.findByRole("radiogroup", { name: "Mode" });
    expect(document.querySelector(".tc-crumb")).toHaveTextContent(/^Board › [0-9a-f]{8}…[0-9a-f]{5}$/);
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
    const put = setup({ palette: "forest", surface: "moss", accent: "violet", colour_amount: "full", derived: true });
    await screen.findByText(/Kraft's default colours until you change one/);
    expect(pressed("Moss")).toBe("true");
    expect(pressed("Violet")).toBe("true");
    expect(screen.getByRole("radio", { name: "Full" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("button", { name: /^Sand/ }));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ surface: "sand", accent: "violet", colour_amount: "full" }));
    expect(screen.queryByText(/Kraft's default colours/)).toBeNull();
  });

  it.each(["green", "amber"] as const)("a stored %s accent is not offered: the page, the paint and the first save fall back to none", async (accent) => {
    const put = setup({ surface: "moss", accent, colour_amount: "full" });
    await screen.findByRole("button", { name: "Blue" });
    for (const gone of ["Green", "Amber"]) expect(screen.queryByRole("button", { name: gone })).toBeNull();
    expect(pressed("None")).toBe("true");
    expect(document.documentElement.dataset.accent).toBe("none");
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Sand/ }));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ surface: "sand" }));
  });

  it("puts the page back and says so when a save fails", async () => {
    setup();
    vi.mocked(api.putTheme).mockRejectedValueOnce(new Error("422: nope"));
    fireEvent.click(await screen.findByRole("button", { name: /^Moss/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: 422: nope");
    expect(pressed("Slate")).toBe("true");
    expect(document.documentElement.dataset.surface).toBe("slate");
  });

  it("shows theme.yaml from the page's state under the header's YAML button, kept current as it changes", async () => {
    setup();
    await screen.findByRole("radiogroup", { name: "Mode" });
    fireEvent.click(screen.getByRole("button", { name: "YAML" }));
    expect(screen.getByLabelText("theme.yaml").textContent).toBe("mode: dark\nsurface: slate\naccent: blue\ncolour_amount: subtle\ncode_scheme:\n  light: auto\n  dark: auto\ndiff:\n  layout: unified\n  colours: theme\n  show_whitespace: true\n  word_highlight: true\n  wrap_lines: false\n  one_file_at_a_time: true\ndensity: compact\nboard:\n  open_in: peek");
    fireEvent.click(screen.getByRole("button", { name: /^Moss/ }));
    await waitFor(() => expect(screen.getByLabelText("theme.yaml").textContent).toContain("surface: moss"));
  });

  it("keeps no side rail until YAML is opened", async () => {
    setup();
    await screen.findByRole("radiogroup", { name: "Mode" });
    expect(screen.queryByRole("complementary")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "YAML" }));
    expect(screen.getByRole("complementary", { name: "appearance pane" })).toBeInTheDocument();
  });

});
