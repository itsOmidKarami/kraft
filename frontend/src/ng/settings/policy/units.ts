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
export const raw = (unit: Unit, v: number | null | undefined): string => (v == null ? "" : unit === "s-as-min" ? String(Math.round((v / 60) * 100) / 100) : String(v));

/** Typed text to the number a `set_value` sends: blank clears (null); units and `$` are tolerated. */
export function parse(unit: Unit, text: string, zero = false): { value: number | null } | { error: string } {
  const t = text.trim().replace(/^\$/, "").replace(/,/g, "").replace(/\s*(min|days?|s)$/i, "");
  if (!t) return { value: null };
  const n = Number(t);
  if (!Number.isFinite(n) || (zero ? n < 0 : n <= 0)) return { error: zero ? "Enter a number, zero or more." : "Enter a number above 0." };
  if (unit === "usd") return { value: n };
  if (unit === "s-as-min") return Number.isInteger(n * 60) ? { value: n * 60 } : { error: "Enter whole seconds' worth of minutes." };
  return Number.isInteger(n) ? { value: n } : { error: "Enter a whole number." };
}
