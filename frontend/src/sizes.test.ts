import { describe, expect, it } from "vitest";
import { humanSize } from "./sizes";

describe("humanSize", () => {
  it.each([[0, "0 KB"], [96 * 1024, "96 KB"], [512 * 1024, "512 KB"], [1.5 * 1024 ** 2, "1.5 MB"], [10 * 1024 ** 3, "10 GB"], [Math.floor(12.1 * 1024 ** 3), "12.1 GB"], [2 * 1024 ** 4, "2 TB"]])("%d reads %s", (n, text) => {
    expect(humanSize(n)).toBe(text);
  });
});
