import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderAt, setupSettingsMocks } from "./testing";

beforeEach(() => {
  setupSettingsMocks();
});

function setPhoneWidth(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

describe("Settings shell phone drill-down (Kraft-j92g)", () => {
  it("is a real index page at desktop width too, a line per section (W7.9)", async () => {
    setPhoneWidth(false);
    renderAt("/settings");
    expect(await screen.findByText("How work runs")).toBeInTheDocument();
    expect(screen.getByText(/Loop caps, concurrency/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Repos" })).toBeNull();
  });

  it("shows the m10 list, not a page body, at phone width", async () => {
    setPhoneWidth(true);
    renderAt("/settings");
    expect(await screen.findByText("How work runs")).toBeInTheDocument();
    expect(await screen.findByText("This instance")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Repos" })).toBeNull();
  });

  it("a row in the phone list opens its page", async () => {
    setPhoneWidth(true);
    renderAt("/settings");
    await userEvent.click(await screen.findByText("Repos"));
    // m12: the phone Repos page carries its own back link to the Settings list
    expect(await screen.findByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
    expect(screen.getByRole("button", { name: "Add repo" })).toBeInTheDocument();
  });
});
