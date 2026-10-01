const scalar = (v: unknown): string => (v == null ? "null" : typeof v === "string" ? (/^[\w./:@-]+$/.test(v) ? v : JSON.stringify(v)) : String(v));

/** A small YAML rendering of effective values, for a screen's YAML toggle when the file itself is not readable here. */
export function yamlOf(o: Record<string, unknown>, pad = ""): string {
  return Object.entries(o)
    .map(([k, v]) => (Array.isArray(v) ? `${pad}${k}: [${v.map(scalar).join(", ")}]` : v && typeof v === "object" ? `${pad}${k}:\n${yamlOf(v as Record<string, unknown>, `${pad}  `)}` : `${pad}${k}: ${scalar(v)}`))
    .join("\n");
}
