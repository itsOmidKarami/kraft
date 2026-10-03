const scalar = (v: unknown): string => (v == null ? "null" : typeof v === "string" ? (/^[\w./:@-]+$/.test(v) ? v : JSON.stringify(v)) : String(v));
const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);

/** One block-sequence entry: an object's first key rides on the dash. */
function entry(v: unknown, pad: string): string {
  if (isObj(v)) return `${pad}- ${yamlOf(v, `${pad}  `).slice(pad.length + 2)}`;
  return `${pad}- ${Array.isArray(v) ? `[${v.map(scalar).join(", ")}]` : scalar(v)}`;
}

/** A small YAML rendering of effective values, for a screen's YAML toggle when the file itself is not readable here. */
export function yamlOf(o: Record<string, unknown>, pad = ""): string {
  return Object.entries(o)
    .map(([k, v]) => {
      if (Array.isArray(v)) return v.some((x) => x && typeof x === "object") ? `${pad}${k}:\n${v.map((x) => entry(x, `${pad}  `)).join("\n")}` : `${pad}${k}: [${v.map(scalar).join(", ")}]`;
      return isObj(v) ? `${pad}${k}:\n${yamlOf(v, `${pad}  `)}` : `${pad}${k}: ${scalar(v)}`;
    })
    .join("\n");
}
