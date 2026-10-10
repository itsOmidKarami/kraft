import { describe, expect, it } from "vitest";
import { humanSize } from "./sizes";

describe("humanSize", () => {
  it.each([[0, "0K"], [512 * 1024, "512K"], [10 * 1024 ** 3, "10G"], [Math.floor(12.1 * 1024 ** 3), "12.1G"]])("%d reads %s", (n, text) => {
    expect(humanSize(n)).toBe(text);
  });
});
