// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = new URL("../../", import.meta.url).pathname;
const OWNERS = ["ng/settings/ForgesPage.tsx", "ng/settings/AddForgeDialog.tsx", "ng/settings/EditForgeDialog.tsx", "ng/settings/forgeForm.tsx", "ng/phone/areas/Forges.tsx"];
const walk = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)]));

describe("the /forges routes", () => {
  it("only the Forge accounts screens ask for /forges", () => {
    const asking = walk(SRC)
      .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f))
      .filter((f) => /["'`]\/forges/.test(readFileSync(f, "utf8")))
      .map((f) => f.slice(SRC.length));
    expect(asking.filter((f) => !OWNERS.includes(f))).toEqual([]);
    expect(asking).toContain("ng/settings/ForgesPage.tsx");
  });
});
