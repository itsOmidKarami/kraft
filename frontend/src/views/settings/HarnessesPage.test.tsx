import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { HarnessProfile, HarnessProviders, Harnesses } from "../../types";
import { renderAt, setupSettingsMocks } from "./testing";
import { setPhoneWidth } from "../../testFixtures";

const profile = (id: string, extra: Partial<HarnessProfile> = {}): HarnessProfile => ({
  id,
  provider: id,
  enabled: true,
  executable: null,
  defaults: {},
  used_by: [],
  chains: [],
  ...extra,
});

const HARNESSES: Harnesses = {
  file: "/home/templates/harnesses.yaml",
  error: null,
  profiles: [
    profile("claude", {
      defaults: { model: "sonnet" },
      used_by: ["tasks.implementer", "tasks.spec_author"],
      chains: ["default"],
    }),
    profile("codex", { enabled: false, executable: "codex", defaults: { effort: "medium" } }),
  ],
};

const cap = (values: string[] = [], always: string | null = null) => ({ values, always, channel: null });
const PROVIDERS: HarnessProviders = {
  valid: {
    claude: {
      id: "claude",
      kind: "cli",
      command: ["claude"],
      path: "/pkg/harnesses/claude.yaml",
      capabilities: { model: cap(), effort: cap(["low", "high", "max"]), permission_mode: cap(["auto"], "auto") },
    },
    codex: {
      id: "codex",
      kind: "cli",
      command: ["codex", "exec"],
      path: "/pkg/harnesses/codex.yaml",
      capabilities: { model: cap(["(gpt|codex).*"]), effort: cap(["low", "medium", "high"]) },
    },
  },
  invalid: { gemini: "gemini.yaml: unknown capability 'x'" },
};

beforeEach(() => {
  setupSettingsMocks();
  vi.spyOn(api, "getHarnesses").mockResolvedValue(HARNESSES);
  vi.spyOn(api, "getHarnessProviders").mockResolvedValue(PROVIDERS);
});

describe("Settings · harnesses", () => {
  it("lists every profile with how many library tasks select it", async () => {
    renderAt("/settings/harnesses");
    expect(await screen.findByRole("button", { name: /claude/ })).toHaveTextContent("2 tasks");
    expect(screen.getByRole("button", { name: /codex/ })).toHaveTextContent("unused · disabled");
    expect(screen.getByText(/gemini: gemini.yaml: unknown capability/)).toBeInTheDocument();
  });

  it("a profile links to the library tasks and chains that select it", async () => {
    renderAt("/settings/harnesses?h=claude");
    expect(await screen.findByRole("heading", { name: "claude" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "tasks.implementer" })).toHaveAttribute(
      "href",
      "/settings/library?c=tasks.implementer",
    );
    expect(screen.getByRole("link", { name: "default" })).toHaveAttribute("href", "/settings/chains?tpl=default");
  });

  it("shows the selected profile's provider capabilities, read-only", async () => {
    renderAt("/settings/harnesses?h=codex");
    expect(await screen.findByRole("heading", { name: "provider codex" })).toBeInTheDocument();
    expect(screen.getByText(/effort values: low \| medium \| high/)).toBeInTheDocument();
    // Only the defaults this provider declares are offered.
    expect(screen.getByLabelText(/default effort/)).toHaveValue("medium");
    expect(screen.queryByLabelText(/default permission_mode/)).not.toBeInTheDocument();
  });

  it("clicking a profile selects it", async () => {
    renderAt("/settings/harnesses");
    await userEvent.click(await screen.findByRole("button", { name: /codex/ }));
    expect(await screen.findByRole("heading", { name: "codex" })).toBeInTheDocument();
  });

  it("saves the edited profile, dropping an emptied default and an empty executable", async () => {
    const put = vi.spyOn(api, "putHarness").mockResolvedValue(HARNESSES.profiles[0]);
    renderAt("/settings/harnesses?h=claude");
    const model = await screen.findByLabelText(/default model/);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.clear(model);
    await userEvent.type(screen.getByLabelText(/default effort/), "high");
    await userEvent.click(screen.getByRole("switch", { name: "disable claude" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith("claude", { provider: "claude", enabled: false, defaults: { effort: "high" } });
    expect(await screen.findByText("saved")).toBeInTheDocument();
  });

  it("a save the server refuses names why and keeps the draft", async () => {
    vi.spyOn(api, "putHarness").mockRejectedValue(
      new Error("chain 'default' task 'impl.main.implementer': profile 'claude' is disabled"),
    );
    renderAt("/settings/harnesses?h=claude");
    await userEvent.click(await screen.findByRole("switch", { name: "disable claude" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/profile 'claude' is disabled/)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "enable claude" })).toBeInTheDocument();
  });

  it("changing the provider offers that provider's defaults", async () => {
    renderAt("/settings/harnesses?h=claude");
    await userEvent.selectOptions(await screen.findByLabelText("provider"), "codex");
    await waitFor(() => expect(screen.getByLabelText(/default effort/)).toBeInTheDocument());
    expect(screen.getByText(/· low \| medium \| high/)).toBeInTheDocument();
  });

  it("Discard drops the draft", async () => {
    renderAt("/settings/harnesses?h=claude");
    const model = await screen.findByLabelText(/default model/);
    await userEvent.type(model, "x");
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(model).toHaveValue("sonnet");
  });

  it("a harnesses.yaml that does not load says why", async () => {
    vi.spyOn(api, "getHarnesses").mockResolvedValue({ ...HARNESSES, profiles: [], error: "harnesses.yaml: bad" });
    renderAt("/settings/harnesses");
    expect(await screen.findByText("harnesses.yaml: bad")).toBeInTheDocument();
  });

  it("phone: the header goes back to Settings", async () => {
    setPhoneWidth(true);
    renderAt("/settings/harnesses");
    expect(await screen.findByRole("link", { name: /Settings/ })).toHaveAttribute("href", "/settings");
  });
});
