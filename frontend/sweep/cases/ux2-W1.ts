import { ng, type Case } from "../cellKit";

export const cells: Case[] = [
  // W1: the token sheet per surface (both modes via the ~light shell), and Appearance's colour section.
  ...["graphite", "slate", "ink", "sand", "moss"].map((surface): Case => ({ screen: "ng-tokens", variant: surface, data: "default", widths: [1280], shells: [{ mode: "light" }], fullPage: true, run: (c) => ng(c, "/ng/_tokens", { surface }) })),
  { screen: "ng-tokens", variant: "moss-mono", data: "default", widths: [1280], shells: [{ mode: "light" }], fullPage: true, run: (c) => ng(c, "/ng/_tokens", { surface: "moss", colour_amount: "mono" }) },
  { screen: "ng-tokens", variant: "graphite-violet-full", data: "default", widths: [1920], fullPage: true, run: (c) => ng(c, "/ng/_tokens", { accent: "violet", colour_amount: "full" }) },
  { screen: "ng-appearance", variant: "default", data: "default", widths: [1280, 1024], run: (c) => ng(c, "/ng/settings/appearance", {}) },
  { screen: "ng-appearance", variant: "mono", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => ng(c, "/ng/settings/appearance", { colour_amount: "mono" }) },
  { screen: "ng-appearance", variant: "blue-full", data: "default", widths: [1280], run: (c) => ng(c, "/ng/settings/appearance", { surface: "slate", accent: "blue", colour_amount: "full" }) },
  // An old theme.yaml with only `palette`: GET /theme derives the look (rule A.3).
  { screen: "ng-appearance", variant: "derived", data: "default", widths: [1280], run: (c) => ng(c, "/ng/settings/appearance", { palette: "forest", surface: "moss", accent: "green", colour_amount: "full", derived: true }) },
];
