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
  ["kraft.toggleAllRepos", "Show All Repos", "$(filter-filled)"],
  ["kraft.showThisRepo", "Show This Repo", "$(filter)"],
])("%s is %s with icon %s", (command, title, icon) =>
  expect(pkg.contributes.commands).toContainEqual({ command, title, category: "Kraft", icon }));

it("offers Show Log in the palette and keeps the status menu out of it", () => {
  const commands = pkg.contributes.commands.map((c: { command: string }) => c.command);
  expect(commands).toEqual(expect.arrayContaining(["kraft.showLog", "kraft.statusMenu"]));
  expect(pkg.contributes.commands).toContainEqual({ command: "kraft.showLog", title: "Show Log", category: "Kraft" });
  expect(pkg.contributes.menus.commandPalette).toContainEqual({ command: "kraft.statusMenu", when: "false" });
});

it("explains each value of kraft.notifications", () => {
  const { enum: values, enumDescriptions } = pkg.contributes.configuration.properties["kraft.notifications"];
  expect(enumDescriptions).toHaveLength(values.length);
});

describe("default keybindings", () => {
  type Binding = { command: string; key: string; mac: string; when: string };
  const keybindings: Binding[] = pkg.contributes.keybindings;
  const titleBar: { command: string; when: string }[] = pkg.contributes.menus["editor/title"];
  const overlap = (a: string, b: string) => a === b || /\b(connected|true)\b/.test(a) || /\b(connected|true)\b/.test(b);

  it.each(keybindings.map((k) => [k.command, k] as const))("%s names a command the extension contributes", (command) =>
    expect(pkg.contributes.commands.map((c: { command: string }) => c.command)).toContain(command));

  it.each(keybindings.map((k) => [k.command, k] as const))("%s has a mac key that is its key with ctrl as cmd", (_, k) =>
    expect(k.mac).toBe(k.key.replace("ctrl", "cmd")));

  it.each(["kraft.approve", "kraft.reject"])("%s works exactly where its title-bar button shows, readOnly aside", (command) => {
    const shown = titleBar.filter((e) => e.command === command).map((e) => e.when.replace(" && !kraft.readOnly", ""));
    const when = keybindings.find((k) => k.command === command)!.when;
    expect(when.split(" || ").sort()).toEqual(shown.flatMap((w) => w.split(" && ").length > 1 ? [w.split(" && ").pop()!] : [w]).sort());
  });

  it("gives no two bindings the same key where both could fire", () => {
    const clashes = keybindings.flatMap((a, i) => keybindings.slice(i + 1).filter((b) => a.key === b.key && overlap(a.when, b.when)));
    expect(clashes).toEqual([]);
  });
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
