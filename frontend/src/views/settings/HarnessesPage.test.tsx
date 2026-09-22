import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { HarnessCapability, HarnessProfile, HarnessProviders, Harnesses } from "../../types";
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

const cap = (values: string[] = [], always: string | string[] | null = null, extra: Partial<HarnessCapability> = {}) => ({
  cli: [],
  values,
  always,
  channel: null,
  source: null,
  reader: null,
  via: null,
  under_allowlist: null,
  ...extra,
});
const PROVIDERS: HarnessProviders = {
  valid: {
    claude: {
      id: "claude",
      kind: "cli",
      command: ["claude"],
      path: "/pkg/harnesses/claude.yaml",
      override: false,
      capabilities: {
        model: cap([], null, { cli: ["--model", "{value}"] }),
        effort: cap(["low", "high", "max"], null, { cli: ["--effort", "{value}"] }),
        deny_tools: cap([], ["Monitor", "Agent"], { cli: ["--disallowed-tools", "{csv}"] }),
        permission_mode: cap(["manual", "auto"], "auto", {
          cli: ["--permission-mode", "{value}"],
          under_allowlist: "manual",
        }),
        usage: cap([], null, { source: "envelope", reader: "claude-stream-json" }),
        frobnicate: cap([], null, { cli: ["--frob"] }),
      },
    },
    codex: {
      id: "codex",
      kind: "cli",
      command: ["codex", "exec"],
      path: "/home/templates/harnesses/codex.yaml",
      override: true,
      capabilities: { model: cap(["(gpt|codex).*"]), effort: cap(["low", "medium", "high"]) },
    },
  },
  invalid: { gemini: "gemini.yaml: unknown capability 'x'" },
};

/** The capability list's row for `name`. */
const row = (name: string) => screen.getByText(name, { selector: ".capability-name" }).closest("li")!;

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
    expect(row("effort")).toHaveTextContent("accepts low | medium | high");
    // Only the defaults this provider declares are offered.
    expect(screen.getByLabelText(/default effort/)).toHaveValue("medium");
    expect(screen.queryByLabelText(/default permission_mode/)).not.toBeInTheDocument();
  });

  it("each capability row says what it means and the flag it becomes (Kraft-ok48k)", async () => {
    renderAt("/settings/harnesses?h=claude");
    expect(await screen.findByText("Capabilities")).toBeInTheDocument();
    expect(screen.getByText(/Kraft's neutral option names, and the flags this CLI receives for them/)).toBeInTheDocument();
    const pm = row("permission_mode");
    expect(pm).toHaveTextContent("--permission-mode <value>");
    expect(pm).toHaveTextContent("accepts manual | auto");
    expect(pm).toHaveTextContent("every launch: auto");
    expect(pm).toHaveTextContent("under a tool allowlist: manual");
    expect(pm).toHaveTextContent("How the CLI decides whether a tool call needs approval");
    expect(row("deny_tools")).toHaveTextContent("--disallowed-tools <a,b,…>");
    expect(row("deny_tools")).toHaveTextContent("every launch: Monitor, Agent");
    expect(row("usage")).toHaveTextContent("read from the output stream (claude-stream-json)");
    // A name Kraft ships no description for renders bare.
    expect(row("frobnicate").textContent).toBe("frobnicate → --frob");
  });

  it("names the packaged definition's path as that, and says how to override it", async () => {
    renderAt("/settings/harnesses?h=claude");
    expect(await screen.findByText("Packaged definition: /pkg/harnesses/claude.yaml")).toBeInTheDocument();
    expect(screen.getByText(/drop a claude.yaml into \$KRAFT_HOME\/templates\/harnesses\//)).toBeInTheDocument();
  });

  it("an override from $KRAFT_HOME is labelled as yours", async () => {
    renderAt("/settings/harnesses?h=codex");
    expect(await screen.findByText("Your override: /home/templates/harnesses/codex.yaml")).toBeInTheDocument();
    expect(screen.queryByText(/Packaged definition/)).not.toBeInTheDocument();
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
