import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import * as toast from "../../ui/Toast";
import type { Theme } from "../../../types";
import { BoardCodeScreen } from "./BoardCode";
import { mountAt } from "./testkit";

const BASE: Theme = { palette: "nocturne", mode: "dark", density: "compact", board: { group_by: "status", show_done: 5, open_in: "peek" }, surface: "slate", accent: "blue", colour_amount: "subtle", derived: false };

function setup(theme: Partial<Theme> = {}) {
  const loaded = { ...BASE, ...theme };
  vi.spyOn(api, "getTheme").mockResolvedValue(loaded);
  let server = loaded;
  const put = vi.spyOn(api, "putTheme").mockImplementation(async (body) => (server = { ...server, ...body, derived: false }));
  mountAt(<BoardCodeScreen />, "/settings/board-and-code", "/settings/board-and-code");
  return put;
}
const row = (name: RegExp) => screen.findByRole("button", { name });
afterEach(() => vi.restoreAllMocks());

describe("Board & code", () => {
  it("says it saves on change and holds no look controls", async () => {
    setup();
    expect(await screen.findByText("Saved on change")).toBeInTheDocument();
    for (const look of [/^Mode/, /^Colour amount/, /^Density/]) expect(screen.queryByRole("button", { name: look })).toBeNull();
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
    await userEvent.click(screen.getByRole("radio", { name: "Side-by-side" }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ diff: expect.objectContaining({ layout: "split", wrap_lines: true }) }));
    await userEvent.click(await row(/^Added and removed colours/));
    await userEvent.click(screen.getByRole("radio", { name: /Colour-blind safe/ }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ diff: expect.objectContaining({ colours: "safe", layout: "split" }) }));
  });

  it("open in is merged into board", async () => {
    const put = setup();
    await userEvent.click(await row(/^Open items in/));
    await userEvent.click(screen.getByRole("radio", { name: "Full page" }));
    await waitFor(() => expect(put).toHaveBeenLastCalledWith({ board: { group_by: "status", show_done: 5, open_in: "full" } }));
  });

  it("a refused choice closes the sheet and says why, with the row unchanged", async () => {
    const put = setup();
    const shown = vi.spyOn(toast, "showToast").mockImplementation(() => {});
    put.mockRejectedValueOnce(new Error("open_in is locked"));
    await userEvent.click(await row(/^Open items in/));
    await userEvent.click(screen.getByRole("radio", { name: "Full page" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: open_in is locked");
    expect(shown).toHaveBeenCalledWith("open_in is locked");
    expect(await row(/^Open items in/)).toHaveTextContent("Peek");
  });
});
