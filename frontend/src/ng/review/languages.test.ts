import { describe, expect, it } from "vitest";
import { createJavaScriptRegexEngine } from "@shikijs/engine-javascript";
import { aliasOf, familyOf, languageOf, LANGS } from "./languages";

const SPEC_60 = ["typescript", "tsx", "javascript", "jsx", "json", "jsonc", "python", "shellscript", "yaml", "toml", "markdown", "css", "scss", "less", "go", "rust", "java", "kotlin", "swift", "c", "cpp", "csharp", "php", "perl", "ruby", "jinja", "html", "xml", "vue", "svelte", "astro", "graphql", "sql", "ini", "diff", "proto", "terraform", "hcl", "nix", "nginx", "docker", "make", "just", "cmake", "lua", "r", "scala", "groovy", "dart", "elixir", "erlang", "haskell", "clojure", "fsharp", "objective-c", "powershell", "bat", "zig", "julia", "latex"];

describe("languages", () => {
  it("lists exactly the spec's 60 languages", () => {
    expect(Object.keys(LANGS).sort()).toEqual([...SPEC_60].sort());
  });

  it.each([
    ["app/models/user.rb", "ruby"],
    ["lib/x.pm", "perl"],
    ["a.pl", "perl"],
    ["index.php", "php"],
    ["A.java", "java"],
    ["s.py", "python"],
    ["t/config.yaml.j2", "jinja"],
    ["page.jinja2", "jinja"],
    ["Dockerfile", "docker"],
    ["Makefile", "make"],
    ["justfile", "just"],
    ["CMakeLists.txt", "cmake"],
    ["x.TSX", "tsx"],
    ["pyproject.toml", "toml"],
    ["x.lock", null],
    ["x.constructor", null],
    ["README", null],
  ])("languageOf(%s) is %s", (path, id) => {
    expect(languageOf(path)).toBe(id);
  });

  it.each([
    ["rb", "ruby"],
    ["PY", "python"],
    ["sh", "shellscript"],
    ["ruby", "ruby"],
    ["", null],
    [undefined, null],
    ["foo", null],
    ["constructor", null],
  ])("aliasOf(%s) is %s", (info, id) => {
    expect(aliasOf(info)).toBe(id);
  });

  it("groups languages for the file tree as the scanner did", () => {
    expect(["search/cache.py", "tests/test_cache.py", "pyproject.toml"].map((p) => familyOf(languageOf(p)))).toEqual(["python", "python", "yaml"]);
    expect(familyOf(null)).toBe("plain");
  });

  it("every mapped grammar compiles under the JavaScript engine", async () => {
    const engine = createJavaScriptRegexEngine();
    const grammars = new Map<string, unknown>();
    for (const load of Object.values(LANGS)) for (const g of (await load()).default) grammars.set((g as { name: string }).name, g);
    expect(grammars.size).toBeGreaterThanOrEqual(73);
    const failures: string[] = [];
    const walk = (name: string, node: unknown) => {
      if (Array.isArray(node)) return node.forEach((n) => walk(name, n));
      if (!node || typeof node !== "object") return;
      for (const [k, v] of Object.entries(node)) {
        if (typeof v === "string" && ["match", "begin", "end", "while"].includes(k)) {
          // end/while may hold \1 backreferences, which Shiki fills in at run time.
          if (k !== "match" && k !== "begin" && /\\\d/.test(v)) continue;
          try {
            engine.createScanner([v]);
          } catch {
            failures.push(`${name}: ${v.slice(0, 60)}`);
          }
        } else walk(name, v);
      }
    };
    for (const [name, g] of grammars) walk(name, g);
    expect(failures).toEqual([]);
  }, 60_000);
});
