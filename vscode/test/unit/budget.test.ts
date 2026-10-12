import { expect, it } from "vitest";
import { parseUsd } from "../../src/core/budget";

it.each([
  ["5", { usd: 5 }],
  [" 2.5 ", { usd: 2.5 }],
  ["none", { usd: null }],
  ["None", { usd: null }],
  ["0", { error: "a positive number of dollars, or `none`, not '0'" }],
  ["-5", { error: "a positive number of dollars, or `none`, not '-5'" }],
  ["", { error: "dollars or `none`, not ''" }],
  ["abc", { error: "dollars or `none`, not 'abc'" }],
  ["inf", { error: "dollars or `none`, not 'inf'" }],
  ["NaN", { error: "dollars or `none`, not 'NaN'" }],
  ["0x10", { error: "dollars or `none`, not '0x10'" }],
])("%j → %j", (raw, want) => {
  expect(parseUsd(raw)).toEqual(want);
});
