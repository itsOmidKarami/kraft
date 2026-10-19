// Renders an SVG to a PNG at its own size: dev/console_screenshot.py turns the console's SVG
// (Textual's save_screenshot) into the PNG the docs show.
//   node e2e/docs-shots/svg-to-png.mjs IN.svg OUT.png
import { chromium } from "@playwright/test";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [svg, png] = process.argv.slice(2);
const box = readFileSync(svg, "utf8").match(/viewBox="0 0 ([\d.]+) ([\d.]+)"/);
if (!svg || !png || !box) throw new Error("usage: svg-to-png.mjs IN.svg OUT.png (an SVG with a viewBox)");
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: Math.ceil(+box[1]), height: Math.ceil(+box[2]) } });
await page.goto(pathToFileURL(svg).href);
// The terminal font comes from a CDN, as Textual's SVG names it.
await page.evaluate(() => document.fonts.ready);
await page.locator("svg").screenshot({ path: png, omitBackground: true });
await browser.close();
