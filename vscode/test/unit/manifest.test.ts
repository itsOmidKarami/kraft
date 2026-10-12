import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

const pkg = JSON.parse(readFileSync(join(__dirname, "../../package.json"), "utf8"));

it("lets only user settings set kraft.url", () =>
  expect(pkg.contributes.configuration.properties["kraft.url"].scope).toBe("machine"));

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
  ["commandPalette", "kraft.fileWork", "!kraft.readOnly"],
  ["commandPalette", "kraft.search", "kraft.connected"],
])("%s offers %s when %s", (menu, command, when) =>
  expect(pkg.contributes.menus[menu]).toContainEqual(expect.objectContaining({ command, when })));

it("offers Show Log in the palette and keeps the status menu out of it", () => {
  const commands = pkg.contributes.commands.map((c: { command: string }) => c.command);
  expect(commands).toEqual(expect.arrayContaining(["kraft.showLog", "kraft.statusMenu"]));
  expect(pkg.contributes.commands).toContainEqual({ command: "kraft.showLog", title: "Show Log", category: "Kraft" });
  expect(pkg.contributes.menus.commandPalette).toContainEqual({ command: "kraft.statusMenu", when: "false" });
});
