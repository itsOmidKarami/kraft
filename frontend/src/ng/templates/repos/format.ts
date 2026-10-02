/** How each Repos Config row reads and writes its value as one line of text.
 *  Every parser answers `{value}` for the patch (null clears the key) or `{error}`. */
export type Parsed = { value: unknown } | { error: string };

const items = (text: string) => text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);

export const list = {
  show: (v: unknown) => (Array.isArray(v) && v.length ? v.join(", ") : ""),
  parse: (text: string): Parsed => ({ value: items(text).length ? items(text) : null }),
};

/** `profile=model; profile=model` ↔ `Repo.models`. */
export const models = {
  show: (v: unknown) => Object.entries((v as Record<string, string> | null) ?? {}).map(([k, m]) => `${k}=${m}`).join("; "),
  parse: (text: string): Parsed => {
    const out: Record<string, string> = {};
    for (const part of text.split(/[;\n]/).map((s) => s.trim()).filter(Boolean)) {
      const at = part.indexOf("=");
      if (at < 1 || at === part.length - 1) return { error: `"${part}" is not profile=model.` };
      out[part.slice(0, at).trim()] = part.slice(at + 1).trim();
    }
    return { value: Object.keys(out).length ? out : null };
  },
};

type Scope = { paths: string[]; command: string };
/** `a/**, b/** => pytest -q; c/** => npm test` ↔ `Repo.test_scopes`. */
export const scopes = {
  show: (v: unknown) => ((v as Scope[] | null) ?? []).map((s) => `${s.paths.join(", ")} => ${s.command}`).join("; "),
  parse: (text: string): Parsed => {
    const out: Scope[] = [];
    for (const part of text.split(";").map((s) => s.trim()).filter(Boolean)) {
      const at = part.indexOf("=>");
      const paths = at < 0 ? [] : items(part.slice(0, at));
      const command = at < 0 ? "" : part.slice(at + 2).trim();
      if (!paths.length || !command) return { error: `"${part}" is not paths => command.` };
      out.push({ paths, command });
    }
    return { value: out.length ? out : null };
  },
};

export const text = {
  show: (v: unknown) => (typeof v === "string" ? v : ""),
  parse: (t: string): Parsed => ({ value: t || null }),
};

/** A command run by a shell (`setup_command`): blank clears it, and `""`
 *  typed in the field is the empty command, "nothing to run", not a command
 *  of two quote marks. */
export const command = {
  show: (v: unknown) => (v === "" ? '""' : typeof v === "string" ? v : ""),
  parse: (t: string): Parsed => ({ value: /^\s*(""|'')\s*$/.test(t) ? "" : t || null }),
};

export const whole = (what: string, positive = true) => ({
  show: (v: unknown) => (v == null ? "" : String(v)),
  parse: (t: string): Parsed => {
    if (!t) return { value: null };
    const n = Number(t);
    return Number.isInteger(n) && (!positive || n > 0) ? { value: n } : { error: `${what} is a whole number above 0.` };
  },
});

export const dollars = {
  show: (v: unknown) => (v == null ? "" : String(v)),
  parse: (t: string): Parsed => {
    if (!t) return { value: null };
    const n = Number(t.replace(/^\$/, ""));
    return n > 0 && Number.isFinite(n) ? { value: n } : { error: "Dollars is a number above 0." };
  },
};
