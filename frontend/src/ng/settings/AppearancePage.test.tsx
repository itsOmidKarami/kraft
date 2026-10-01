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
    await screen.findByText(/Kraft's default colours until you change one/);
    expect(pressed("Moss")).toBe("true");
    expect(pressed("Green")).toBe("true");
    expect(screen.getByRole("radio", { name: "Full" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("button", { name: /^Sand/ }));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ surface: "sand", accent: "green", colour_amount: "full" }));
    expect(screen.queryByText(/Kraft's default colours/)).toBeNull();
  });

  it("puts the page back and says so when a save fails", async () => {
    setup();
    vi.mocked(api.putTheme).mockRejectedValueOnce(new Error("422: nope"));
    fireEvent.click(await screen.findByRole("button", { name: /^Moss/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: 422: nope");
    expect(pressed("Slate")).toBe("true");
    expect(document.documentElement.dataset.surface).toBe("slate");
  });

  describe("code scheme, review diff, density, board", () => {
    it("writes code_scheme whole, one mode changed, and paints data-code", async () => {
      const put = setup({ code_scheme: { light: "auto", dark: "auto" } });
      fireEvent.click(await screen.findByRole("button", { name: "dark scheme: Monokai" }));
      await waitFor(() => expect(put).toHaveBeenCalledWith({ code_scheme: { light: "auto", dark: "monokai" } }));
      expect(document.documentElement.dataset.code).toBe("monokai");
      fireEvent.click(screen.getByRole("button", { name: "light scheme: Solarized Light" }));
      await waitFor(() => expect(put).toHaveBeenLastCalledWith({ code_scheme: { light: "solarized-light", dark: "monokai" } }));
      expect(screen.getByRole("button", { name: "light scheme: Solarized Light" })).toHaveAttribute("aria-pressed", "true");
      expect(screen.queryByRole("button", { name: "light scheme: Monokai" })).toBeNull();
    });

    it("None is its own choice, not Auto", async () => {
      const put = setup();
      fireEvent.click(await screen.findByRole("button", { name: "dark scheme: None" }));
      await waitFor(() => expect(put).toHaveBeenCalledWith({ code_scheme: { light: "auto", dark: "none" } }));
      expect(document.documentElement.dataset.code).toBeUndefined();
    });

    it("sends the whole diff object with only the changed key different", async () => {
      const put = setup({ diff: { layout: "unified", colours: "theme", show_whitespace: true, word_highlight: true, wrap_lines: false, one_file_at_a_time: true } });
      fireEvent.click(await screen.findByRole("radio", { name: "Side by side" }));
      fireEvent.click(screen.getByRole("button", { name: "Diff colours: Colour-blind safe" }));
      fireEvent.click(screen.getByRole("switch", { name: "Wrap long lines" }));
      await waitFor(() => expect(put).toHaveBeenCalledTimes(3));
      const base = { layout: "unified", colours: "theme", show_whitespace: true, word_highlight: true, wrap_lines: false, one_file_at_a_time: true };
      expect(put.mock.calls.map((c) => c[0])).toEqual([
        { diff: { ...base, layout: "split" } },
        { diff: { ...base, layout: "split", colours: "safe" } },
        { diff: { ...base, layout: "split", colours: "safe", wrap_lines: true } },
      ]);
    });

    it("saves density and open-in alone, keeping the rest of board", async () => {
      const put = setup();
      fireEvent.click(await screen.findByRole("radio", { name: "Comfortable" }));
      fireEvent.click(screen.getByRole("radio", { name: "Full page" }));
      await waitFor(() => expect(put).toHaveBeenCalledTimes(2));
      expect(put.mock.calls.map((c) => c[0])).toEqual([{ density: "comfortable" }, { board: { group_by: "status", show_done: 5, open_in: "full" } }]);
    });

    it("puts a diff switch back when the save fails", async () => {
      setup();
      vi.mocked(api.putTheme).mockRejectedValueOnce(new Error("422: nope"));
      fireEvent.click(await screen.findByRole("switch", { name: "Wrap long lines" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: 422: nope");
      expect(screen.getByRole("switch", { name: "Wrap long lines" })).toHaveAttribute("aria-checked", "false");
    });
  });
});
