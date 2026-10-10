import { dollars, DOLLARS_HINT, dollarsText } from "../../../format";
import type { Unit } from "./types";

/** A number as the page writes it (the prototype's `fmt`). */
export function show(unit: Unit, v: number | null | undefined, bound = false): string {
  if (v == null) return bound ? "no bound" : "not set";
  switch (unit) {
    case "usd": return `$${v}`;
    case "tok": return v.toLocaleString("en-US");
    case "min": return `${v} min`;
    case "days": return `${v} ${v === 1 ? "day" : "days"}`;
    case "s": return `${v} s`;
    case "s-as-min": return `${Math.round((v / 60) * 100) / 100} min`;
    default: return String(v);
  }
}

/** What the input starts with. */
export const raw = (unit: Unit, v: number | null | undefined): string =>
  v == null ? "" : unit === "s-as-min" ? String(Math.round((v / 60) * 100) / 100) : unit === "usd" ? dollarsText(v) : String(v);

/** Typed text to the number a `set_value` sends: blank clears (null); units and `$` are tolerated. */
export function parse(unit: Unit, text: string, zero = false): { value: number | null } | { error: string } {
  const typed = text.trim().replace(/^\$/, "").replace(/\s*(min|days?|s)$/i, "");
  if (!typed) return { value: null };
  // Dollars read as the budget editor reads them: stripping commas saved "0,5" as $5 (r12 review).
  // Every other unit is a count, where a comma only groups thousands ("100,000" tokens).
  if (unit === "usd") {
    const usd = dollars(typed);
    if (Number.isNaN(usd)) return { error: DOLLARS_HINT };
    return usd > 0 || (zero && usd === 0) ? { value: usd } : { error: zero ? "Enter a number, zero or more." : "Enter a number above 0." };
  }
  const t = typed.replace(/,/g, "");
  const n = /^(\d+(\.\d+)?|\.\d+)$/.test(t) ? Number(t) : NaN;
  if (!Number.isFinite(n) || (zero ? n < 0 : n <= 0)) return { error: zero ? "Enter a number, zero or more." : "Enter a number above 0." };
  if (unit === "s-as-min") return Number.isInteger(n * 60) ? { value: n * 60 } : { error: "Enter whole seconds' worth of minutes." };
  return Number.isInteger(n) ? { value: n } : { error: "Enter a whole number." };
}

/** A clean-up age as `policy.yaml` writes it (`24h`, `2d`): blank turns it off. */
export function parseAge(text: string): { value: string | null } | { error: string } {
  const typed = text.trim();
  if (!typed) return { value: null };
  return /^(0|[1-9]\d*)[hd]$/i.test(typed) ? { value: typed.toLowerCase() } : { error: "Enter hours or days: 12h, 24h, 2d." };
}

/** A storage size as `policy.yaml` writes it (`10G`): blank clears; anything else needs a whole number and a unit. */
export function parseSize(text: string): { value: string | null } | { error: string } {
  const typed = text.trim();
  if (!typed) return { value: null };
  return /^[1-9]\d*[kmgt]b?$/i.test(typed) ? { value: typed.toUpperCase() } : { error: "Enter a whole number with a unit: 1000M, 10G, 1T." };
}
