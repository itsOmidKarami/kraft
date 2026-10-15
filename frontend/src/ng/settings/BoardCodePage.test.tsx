import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import * as http from "../http";
import type { Theme } from "../../types";
import { BoardCodePage } from "./BoardCodePage";
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
const DIFF = { layout: "unified", colours: "theme", show_whitespace: true, word_highlight: true, wrap_lines: false, one_file_at_a_time: true } as const;

function setup(theme: Partial<Theme> = {}) {
  const loaded = { ...BASE, ...theme };
  vi.spyOn(api, "getTheme").mockResolvedValue(loaded);
  // As the server does: merge into the file, answer with the whole of it.
  let server = loaded;
  const put = vi.spyOn(api, "putTheme").mockImplementation(async (body) => (server = { ...server, ...body, derived: false }));
  render(<WithHeader><BoardCodePage /></WithHeader>);
  return put;
}

afterEach(() => vi.restoreAllMocks());

describe("ng BoardCodePage", () => {
  it("lays out the code and board choices as open sections under one heading, with no look controls", async () => {
    setup();
    await screen.findByRole("heading", { level: 1, name: "Board & code" });
    expect(screen.getByText("Saved on change")).toHaveClass("saved-note");
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(["Syntax highlighting", "Review diff", "Board", "Default editor"]);
    expect(document.querySelector("details")).toBeNull();
    for (const look of ["Mode", "Surface", "Accent", "Density"]) expect(screen.queryByRole("radiogroup", { name: look })).toBeNull();
  });

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
    const put = setup({ diff: DIFF });
    fireEvent.click(await screen.findByRole("radio", { name: "Side-by-side" }));
    fireEvent.click(screen.getByRole("button", { name: "Diff colours: Colour-blind safe" }));
    fireEvent.click(screen.getByRole("switch", { name: "Wrap long lines" }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(3));
    expect(put.mock.calls.map((c) => c[0])).toEqual([
      { diff: { ...DIFF, layout: "split" } },
      { diff: { ...DIFF, layout: "split", colours: "safe" } },
      { diff: { ...DIFF, layout: "split", colours: "safe", wrap_lines: true } },
    ]);
  });

  it("saves open-in alone, keeping the rest of board", async () => {
    const put = setup();
    fireEvent.click(await screen.findByRole("radio", { name: "Full page" }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    expect(put.mock.calls[0][0]).toEqual({ board: { group_by: "status", show_done: 5, open_in: "full" } });
  });

  it("puts a diff switch back when the save fails", async () => {
    setup();
    vi.mocked(api.putTheme).mockRejectedValueOnce(new Error("422: nope"));
    fireEvent.click(await screen.findByRole("switch", { name: "Wrap long lines" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: 422: nope");
    expect(screen.getByRole("switch", { name: "Wrap long lines" })).toHaveAttribute("aria-checked", "false");
  });

  it.each([
    // r12 review: unset is KRAFT_EDITOR first, so it is "Not set", not "System default".
    ["a choice this machine has", "zed", ["VS Code", "Zed", "Not set"], "Zed"],
    ["none set", null, ["VS Code", "Zed", "Not set"], "Not set"],
    ["a choice this machine no longer has, still shown", "obsidian", ["VS Code", "Zed", "Obsidian", "Not set"], "Obsidian"],
  ])("lists the editors found on this machine for the default editor, with %s selected", async (_, editor, names, selected) => {
    vi.spyOn(http, "request").mockResolvedValue({ status: 200, body: { available: ["code", "zed"], system: true, default: editor } });
    const put = setup({ editor });
    const group = await screen.findByRole("radiogroup", { name: "Default editor" });
    expect(within(group).getAllByRole("radio").map((r) => r.textContent)).toEqual(names);
    expect(within(group).getByRole("radio", { name: selected })).toBeChecked();
    fireEvent.click(within(group).getByRole("radio", { name: "VS Code" }));
    fireEvent.click(within(group).getByRole("radio", { name: "Not set" }));
    await waitFor(() => expect(put.mock.calls.map((c) => c[0])).toEqual([{ editor: "code" }, { editor: null }]));
  });

  // R14b-03: the preview's lines scroll sideways, so a keyboard has to be able to reach them.
  it("lets the keyboard reach the diff preview's scrolling lines", async () => {
    setup();
    const lines = await screen.findByRole("group", { name: /^Diff preview lines/ });
    expect(lines).toHaveAttribute("tabindex", "0");
    expect(lines).toHaveClass("set-diff-body");
  });

  it("writes the chosen code schemes and diff preferences into the YAML, not the defaults", async () => {
    setup({ code_scheme: { light: "none", dark: "monokai" }, diff: { ...DIFF, layout: "split", colours: "safe", show_whitespace: false, word_highlight: false, wrap_lines: true, one_file_at_a_time: false }, editor: "zed" });
    await screen.findByRole("heading", { level: 1, name: "Board & code" });
    fireEvent.click(screen.getByRole("button", { name: "YAML" }));
    expect(screen.getByRole("complementary", { name: "board & code pane" })).toBeInTheDocument();
    const yaml = screen.getByLabelText("theme.yaml").textContent;
    expect(yaml).toContain("code_scheme:\n  light: none\n  dark: monokai\ndiff:\n  layout: split\n  colours: safe\n  show_whitespace: false\n  word_highlight: false\n  wrap_lines: true\n  one_file_at_a_time: false\n");
    expect(yaml).toContain("board:\n  open_in: peek\neditor: zed");
  });
});
