import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { MUTATING_PRIMARY, PRIMARY_COMMAND, primaryToken, type Primary } from "../../src/core/primary";

const pkg = JSON.parse(readFileSync(join(__dirname, "../../package.json"), "utf8"));

it.each(["kraft.url", "kraft.path"])("lets only user settings set %s", (key) =>
  expect(pkg.contributes.configuration.properties[key].scope).toBe("machine"));

it("declares limited support in untrusted workspaces", () =>
  expect(pkg.capabilities).toEqual({ untrustedWorkspaces: {
    supported: "limited",
    description: "In Restricted Mode Kraft shows the board and gates read-only and does not check config files in the workspace.",
    restrictedConfigurations: ["kraft.url"],
  } }));

it.each(["kraft.lintLibrary", "kraft.showResolved"])("hides %s from the palette until trusted", (command) =>
  expect(pkg.contributes.menus.commandPalette).toContainEqual({ command, when: "isWorkspaceTrusted" }));

it.each([
  ["explorer/context", "kraft.fileAsSpec", "resourceExtname == .md && !explorerResourceIsFolder && !kraft.readOnly"],
  ["explorer/context", "kraft.fileAsPlan", "resourceExtname == .md && !explorerResourceIsFolder && !kraft.readOnly"],
  ["editor/context", "kraft.fileSelection", "editorHasSelection && !kraft.readOnly"],
  ["view/title", "kraft.fileWork", "view == kraft.board && !kraft.readOnly"],
  ["view/title", "kraft.toggleAllRepos", "view == kraft.board && !kraft.allRepos"],
  ["view/title", "kraft.showThisRepo", "view == kraft.board && kraft.allRepos"],
  ["commandPalette", "kraft.toggleAllRepos", "!kraft.allRepos"],
  ["commandPalette", "kraft.showThisRepo", "kraft.allRepos"],
  ["commandPalette", "kraft.fileWork", "!kraft.readOnly"],
  ["commandPalette", "kraft.search", "kraft.connected"],
  ["commandPalette", "kraft.approveNextGate", "kraft.connected"],
  ["commandPalette", "kraft.openItem", "kraft.connected"],
  ["editor/title", "kraft.approve", "resourceScheme == webview-panel && kraft.artifactPreview && !kraft.readOnly"],
  ["editor/title", "kraft.reject", "resourceScheme == webview-panel && kraft.artifactPreview && !kraft.readOnly"],
  ["editor/title", "kraft.review.submit", "resourceScheme == webview-panel && kraft.artifactPreview && !kraft.readOnly"],
  ["editor/title", "kraft.reviewChanges", "resourceScheme == webview-panel && kraft.artifactPreview"],
])("%s offers %s when %s", (menu, command, when) =>
  expect(pkg.contributes.menus[menu]).toContainEqual(expect.objectContaining({ command, when })));

it.each([
  ["kraft.toggleAllRepos", "Show All Repos", "$(filter)"],
  ["kraft.showThisRepo", "Show This Repo", "$(filter-filled)"],
])("%s is %s with icon %s", (command, title, icon) =>
  expect(pkg.contributes.commands).toContainEqual({ command, title, category: "Kraft", icon }));

it("offers Show Log in the palette and keeps the status menu out of it", () => {
  const commands = pkg.contributes.commands.map((c: { command: string }) => c.command);
  expect(commands).toEqual(expect.arrayContaining(["kraft.showLog", "kraft.statusMenu"]));
  expect(pkg.contributes.commands).toContainEqual({ command: "kraft.showLog", title: "Show Log", category: "Kraft" });
  expect(pkg.contributes.menus.commandPalette).toContainEqual({ command: "kraft.statusMenu", when: "false" });
});

it.each([
  ["kraft.cancel", "Cancel…"], ["kraft.skip", "Skip…"], ["kraft.archive", "Archive…"],
  ["kraft.review.discard", "Discard Review Comments…"], ["kraft.review.deleteComment", "Delete…"],
  ["kraft.review.otherThreads", "Show Other Threads"], ["kraft.approveNextGate", "Next Gate"],
])("%s is titled %s", (command, title) =>
  expect(pkg.contributes.commands.find((c: { command: string }) => c.command === command)?.title).toBe(title));

it("explains each value of kraft.notifications", () => {
  const { enum: values, enumDescriptions } = pkg.contributes.configuration.properties["kraft.notifications"];
  expect(enumDescriptions).toHaveLength(values.length);
});

describe("default keybindings", () => {
  const keybindings = pkg.contributes.keybindings as { command: string; key: string; mac: string; when: string }[];
  const gateDoc = "resourceScheme == kraft-artifact || kraft.artifactPreview";
  it.each([
    ["kraft.approveNextGate", "ctrl+alt+k n", "kraft.connected"],
    ["kraft.approve", "ctrl+alt+k a", gateDoc],
    ["kraft.reject", "ctrl+alt+k r", gateDoc],
    ["kraft.review.submit", "ctrl+alt+k s", "kraft.artifactPreview || resourceScheme =~ /^kraft-(wt|git|artifact|threads)$/ && kraft.review.activeHasDrafts"],
  ])("%s is %s, only when %s", (command, key, when) =>
    expect(keybindings.filter((k) => k.command === command)).toEqual([{ command, key, mac: key.replace("ctrl", "cmd"), when }]));
});

describe("a row's primary action", () => {
  const commands = pkg.contributes.commands as { command: string; icon?: string }[];
  const board = pkg.contributes.menus["view/item/context"] as { command: string; when?: string; group?: string }[];
  it.each(Object.keys(PRIMARY_COMMAND) as Primary[])("%s is an inline button with an icon", (p) => {
    const command = PRIMARY_COMMAND[p];
    expect(commands.find((c) => c.command === command)?.icon).toMatch(/^\$\([\w-]+\)$/);
    const when = `view == kraft.board && viewItem =~ /\\b${primaryToken(p)}\\b/${MUTATING_PRIMARY.has(p) ? " && !kraft.readOnly" : ""}`;
    expect(board.filter((e) => e.group === "inline" && e.command === command)).toEqual([{ command, when, group: "inline" }]);
  });

  it("Answer is a command, in the palette only while writable", () => {
    expect(pkg.contributes.commands).toContainEqual({ command: "kraft.answer", title: "Answer…", category: "Kraft", icon: "$(comment)" });
    expect(pkg.contributes.menus.commandPalette).toContainEqual({ command: "kraft.answer", when: "kraft.connected && !kraft.readOnly" });
  });
});
