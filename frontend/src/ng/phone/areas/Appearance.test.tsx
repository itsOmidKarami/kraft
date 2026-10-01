import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import type { Theme } from "../../../types";
import { AppearanceScreen } from "./Appearance";
import { mountAt } from "./testkit";

const BASE: Theme = { palette: "nocturne", mode: "dark", density: "compact", board: { group_by: "status", show_done: 5, open_in: "peek" }, surface: "slate", accent: "blue", colour_amount: "subtle", derived: false };

function setup(theme: Partial<Theme> = {}) {
  const loaded = { ...BASE, ...theme };
  vi.spyOn(api, "getTheme").mockResolvedValue(loaded);
  // As the server does: merge into the file, answer with the whole of it.
  let server = loaded;
  const put = vi.spyOn(api, "putTheme").mockImplementation(async (body) => (server = { ...server, ...body, derived: false }));
  mountAt(<AppearanceScreen />, "/settings/appearance", "/settings/appearance");
  return put;
}
const row = (name: RegExp) => screen.findByRole("button", { name });
const radio = (name: RegExp) => screen.findByRole("radio", { name });
afterEach(() => vi.restoreAllMocks());

describe("Appearance (O.4)", () => {
  it("sends only the key each control changes, and repaints at once", async () => {
    const put = setup();
    await userEvent.click(await row(/^Mode/));
    await userEvent.click(screen.getByRole("radio", { name: "Light" }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ mode: "light" }));
    await userEvent.click(await row(/^Colour amount/));
    await userEvent.click(screen.getByRole("radio", { name: "Full" }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ colour_amount: "full" }));
    await userEvent.click(await radio(/^Moss/));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ surface: "moss" }));
    await userEvent.click(await radio(/^Rose/));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ accent: "rose" }));
    expect(put).toHaveBeenCalledTimes(4);
    expect(document.documentElement.dataset).toMatchObject({ surface: "moss", accent: "rose", amount: "full", mode: "light" });
  });

  it("paints a change before the server answers", async () => {
    setup();
    vi.mocked(api.putTheme).mockReturnValueOnce(new Promise(() => {}));
    await userEvent.click(await radio(/^Ink/));
    expect(document.documentElement.dataset.surface).toBe("ink");
  });

  it("the surface rows show the chosen one with a check, and a swatch from the tokens", async () => {
    setup();
    const slate = await radio(/^Slate/);
    expect(slate).toHaveAttribute("aria-checked", "true");
    expect(slate).toHaveTextContent("✓");
    expect(screen.getByRole("radio", { name: /^Graphite/ })).toHaveAttribute("aria-checked", "false");
    expect(slate.querySelector(".ph-swatch")).toHaveAttribute("data-surface", "slate");
  });

  it("Mono clears the accent and locks the accent rows with the reason", async () => {
    const put = setup();
    await userEvent.click(await row(/^Colour amount/));
    await userEvent.click(screen.getByRole("radio", { name: "Mono" }));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ colour_amount: "mono", accent: "none" }));
    const accents = await screen.findByRole("region", { name: "Accent" });
    expect(within(accents).getByRole("radio", { name: "Blue" })).toBeDisabled();
    expect(within(accents).getByRole("radio", { name: "None" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(/Mono has no accent/)).toBeInTheDocument();
  });

  it("a refused save puts the control back and says why", async () => {
    const put = setup();
    put.mockRejectedValueOnce(new Error("accent blue is not allowed"));
    await userEvent.click(await radio(/^Moss/));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: accent blue is not allowed");
    expect(screen.getByRole("radio", { name: /^Slate/ })).toHaveAttribute("aria-checked", "true");
    expect(document.documentElement.dataset.surface).toBe("slate");
  });

  it("a refused choice from a sheet closes it and says why, with the row unchanged", async () => {
    const put = setup();
    put.mockRejectedValueOnce(new Error("mode is locked"));
    await userEvent.click(await row(/^Mode/));
    await userEvent.click(screen.getByRole("radio", { name: "Light" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: mode is locked");
    expect(await row(/^Mode/)).toHaveTextContent("Dark");
  });

  it("a derived look is written out whole on its first change", async () => {
    const put = setup({ derived: true });
    await userEvent.click(await radio(/^Moss/));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ surface: "moss", accent: "blue", colour_amount: "subtle" }));
  });

  it("the syntax schemes are chosen per mode and go whole", async () => {
    const put = setup();
    await userEvent.click(await row(/^Dark scheme/));
    await userEvent.click(screen.getByRole("radio", { name: "Dracula" }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ code_scheme: { light: "auto", dark: "dracula" } }));
    await userEvent.click(await row(/^Light scheme/));
    expect(screen.queryByRole("radio", { name: "Dracula" })).toBeNull();
    await userEvent.click(screen.getByRole("radio", { name: "Solarized Light" }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ code_scheme: { light: "solarized-light", dark: "dracula" } }));
  });

  it("the review diff choices send the whole diff object with one key changed", async () => {
    const put = setup({ diff: { layout: "unified", colours: "theme", show_whitespace: true, word_highlight: true, wrap_lines: false, one_file_at_a_time: true } });
    await userEvent.click(await screen.findByRole("switch", { name: /Wrap long lines/ }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ diff: { layout: "unified", colours: "theme", show_whitespace: true, word_highlight: true, wrap_lines: true, one_file_at_a_time: true } }));
    await userEvent.click(await row(/^Layout/));
    await userEvent.click(screen.getByRole("radio", { name: "Side by side" }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ diff: expect.objectContaining({ layout: "split", wrap_lines: true }) }));
    await userEvent.click(await row(/^Added and removed colours/));
    await userEvent.click(screen.getByRole("radio", { name: /Colour-blind safe/ }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ diff: expect.objectContaining({ colours: "safe", layout: "split" }) }));
  });

  it("the board choices: density and open in (merged into board)", async () => {
    const put = setup();
    await userEvent.click(await row(/^Density/));
    await userEvent.click(screen.getByRole("radio", { name: "Comfortable" }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ density: "comfortable" }));
    await userEvent.click(await row(/^Open items in/));
    await userEvent.click(screen.getByRole("radio", { name: "Full page" }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ board: { group_by: "status", show_done: 5, open_in: "full" } }));
  });

  it("YAML shows the effective values", async () => {
    setup();
    mountAt(<AppearanceScreen />, "/settings/appearance?yaml=1", "/settings/appearance");
    expect((await screen.findAllByText(/^palette: nocturne/))[0].textContent).toContain("surface: slate");
  });
});
