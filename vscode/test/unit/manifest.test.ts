import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

const pkg = JSON.parse(readFileSync(join(__dirname, "../../package.json"), "utf8"));

it("lets only user settings set kraft.url", () =>
  expect(pkg.contributes.configuration.properties["kraft.url"].scope).toBe("machine"));
