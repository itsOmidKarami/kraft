import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, setupSettingsMocks } from "./testing";

beforeEach(() => {
  setupSettingsMocks();
});

describe("Settings · appearance", () => {
  it("lists all 5 palettes and the light/dark/system control", async () => {
    renderAt("/settings/appearance");
    expect(
      await screen.findByRole("button", { name: /Nocturne/, pressed: true }),
    ).toBeInTheDocument();
    for (const name of ["Rose", "Forest", "Amber", "Slate"]) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
    expect(screen.getByRole("radiogroup", { name: /mode/i })).toBeInTheDocument();
  });

  it("previews live on click and saves on Save", async () => {
    const put = vi.spyOn(api, "putTheme").mockResolvedValue({
      palette: "forest",
      mode: "dark",
      density: "compact",
      board: { group_by: "status", show_done: 5, open_in: "peek" },
    });
    renderAt("/settings/appearance");
    await screen.findByRole("button", { name: /Nocturne/, pressed: true });

    fireEvent.click(screen.getByText("Forest"));
    expect(document.documentElement.dataset.palette).toBe("forest");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith(
      expect.objectContaining({ palette: "forest", mode: "dark" }),
    );
  });

  it("previews density live and saves it", async () => {
    const put = vi.spyOn(api, "putTheme").mockResolvedValue({
      palette: "nocturne",
      mode: "dark",
      density: "comfortable",
      board: { group_by: "status", show_done: 5, open_in: "peek" },
    });
    renderAt("/settings/appearance");
    await screen.findByRole("button", { name: /Nocturne/, pressed: true });
    await userEvent.click(screen.getByRole("radio", { name: "Comfortable" }));
    expect(document.documentElement.dataset.density).toBe("comfortable");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith(expect.objectContaining({ density: "comfortable" }));
  });

  it("changes the board group-by preference", async () => {
    const put = vi.spyOn(api, "putTheme").mockResolvedValue({
      palette: "nocturne",
      mode: "dark",
      density: "compact",
      board: { group_by: "repo", show_done: 5, open_in: "peek" },
    });
    renderAt("/settings/appearance");
    await screen.findByRole("button", { name: /Nocturne/, pressed: true });
    await userEvent.click(screen.getByRole("radio", { name: "repo" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith(
      expect.objectContaining({ board: { group_by: "repo", show_done: 5, open_in: "peek" } }),
    );
  });

  it("disables the palette, mode and density controls until the theme loads", async () => {
    vi.spyOn(api, "getTheme").mockImplementation(() => new Promise(() => {}));
    renderAt("/settings/appearance");
    expect(await screen.findByText("Nocturne")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Nocturne/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Light" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Compact" })).toBeDisabled();
  });

  it("Discard reverts the live preview back to the loaded value", async () => {
    renderAt("/settings/appearance");
    await screen.findByRole("button", { name: /Nocturne/, pressed: true });

    fireEvent.click(screen.getByText("Rose"));
    expect(document.documentElement.dataset.palette).toBe("rose");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Discard" }));
    expect(document.documentElement.dataset.palette).toBe("nocturne");
  });
});
