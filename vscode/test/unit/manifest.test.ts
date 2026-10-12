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
