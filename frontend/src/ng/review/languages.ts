import type { LanguageRegistration } from "shiki/core";

/** The languages Kraft ships grammars for. Each is its own chunk (a literal
 *  `import()` apiece), loaded only when a diff or comment needs it. Adding one
 *  is a line here and a line in `EXT` or `ALIAS` below. */
export type LangId =
  | "typescript"
  | "tsx"
  | "javascript"
  | "jsx"
  | "json"
  | "jsonc"
  | "python"
  | "shellscript"
  | "yaml"
  | "toml"
  | "markdown"
  | "css"
  | "scss"
  | "less"
  | "go"
  | "rust"
  | "java"
  | "kotlin"
  | "swift"
  | "c"
  | "cpp"
  | "csharp"
  | "php"
  | "perl"
  | "ruby"
  | "jinja"
  | "html"
  | "xml"
  | "vue"
  | "svelte"
  | "astro"
  | "graphql"
  | "sql"
  | "ini"
  | "diff"
  | "proto"
  | "terraform"
  | "hcl"
  | "nix"
  | "nginx"
  | "docker"
  | "make"
  | "just"
  | "cmake"
  | "lua"
  | "r"
  | "scala"
  | "groovy"
  | "dart"
  | "elixir"
  | "erlang"
  | "haskell"
  | "clojure"
  | "fsharp"
  | "objective-c"
  | "powershell"
  | "bat"
  | "zig"
  | "julia"
  | "latex";

type Grammar = () => Promise<{ default: LanguageRegistration[] }>;

export const LANGS: Record<LangId, Grammar> = {
  typescript: () => import("@shikijs/langs/typescript"),
  tsx: () => import("@shikijs/langs/tsx"),
  javascript: () => import("@shikijs/langs/javascript"),
  jsx: () => import("@shikijs/langs/jsx"),
  json: () => import("@shikijs/langs/json"),
  jsonc: () => import("@shikijs/langs/jsonc"),
  python: () => import("@shikijs/langs/python"),
  shellscript: () => import("@shikijs/langs/shellscript"),
  yaml: () => import("@shikijs/langs/yaml"),
  toml: () => import("@shikijs/langs/toml"),
  markdown: () => import("@shikijs/langs/markdown"),
  css: () => import("@shikijs/langs/css"),
  scss: () => import("@shikijs/langs/scss"),
  less: () => import("@shikijs/langs/less"),
  go: () => import("@shikijs/langs/go"),
  rust: () => import("@shikijs/langs/rust"),
  java: () => import("@shikijs/langs/java"),
  kotlin: () => import("@shikijs/langs/kotlin"),
  swift: () => import("@shikijs/langs/swift"),
  c: () => import("@shikijs/langs/c"),
  cpp: () => import("@shikijs/langs/cpp"),
  csharp: () => import("@shikijs/langs/csharp"),
  php: () => import("@shikijs/langs/php"),
  perl: () => import("@shikijs/langs/perl"),
  ruby: () => import("@shikijs/langs/ruby"),
  jinja: () => import("@shikijs/langs/jinja"),
  html: () => import("@shikijs/langs/html"),
  xml: () => import("@shikijs/langs/xml"),
  vue: () => import("@shikijs/langs/vue"),
  svelte: () => import("@shikijs/langs/svelte"),
  astro: () => import("@shikijs/langs/astro"),
  graphql: () => import("@shikijs/langs/graphql"),
  sql: () => import("@shikijs/langs/sql"),
  ini: () => import("@shikijs/langs/ini"),
  diff: () => import("@shikijs/langs/diff"),
  proto: () => import("@shikijs/langs/proto"),
  terraform: () => import("@shikijs/langs/terraform"),
  hcl: () => import("@shikijs/langs/hcl"),
  nix: () => import("@shikijs/langs/nix"),
  nginx: () => import("@shikijs/langs/nginx"),
  docker: () => import("@shikijs/langs/docker"),
  make: () => import("@shikijs/langs/make"),
  just: () => import("@shikijs/langs/just"),
  cmake: () => import("@shikijs/langs/cmake"),
  lua: () => import("@shikijs/langs/lua"),
  r: () => import("@shikijs/langs/r"),
  scala: () => import("@shikijs/langs/scala"),
  groovy: () => import("@shikijs/langs/groovy"),
  dart: () => import("@shikijs/langs/dart"),
  elixir: () => import("@shikijs/langs/elixir"),
  erlang: () => import("@shikijs/langs/erlang"),
  haskell: () => import("@shikijs/langs/haskell"),
  clojure: () => import("@shikijs/langs/clojure"),
  fsharp: () => import("@shikijs/langs/fsharp"),
  "objective-c": () => import("@shikijs/langs/objective-c"),
  powershell: () => import("@shikijs/langs/powershell"),
  bat: () => import("@shikijs/langs/bat"),
  zig: () => import("@shikijs/langs/zig"),
  julia: () => import("@shikijs/langs/julia"),
  latex: () => import("@shikijs/langs/latex"),
};

/** Own keys only: a file named `x.constructor` is not a language. */
const own = (table: Record<string, LangId>, key: string): LangId | null => (Object.hasOwn(table, key) ? table[key] : null);

/** File extension (lower-case, no dot) to language. */
const EXT: Record<string, LangId> = {};
for (const [id, exts] of Object.entries({
  typescript: "ts mts cts",
  tsx: "tsx",
  javascript: "js mjs cjs",
  jsx: "jsx",
  json: "json",
  jsonc: "jsonc",
  python: "py pyi pyw",
  shellscript: "sh bash zsh ksh",
  yaml: "yaml yml",
  toml: "toml",
  markdown: "md markdown mdx",
  css: "css",
  scss: "scss",
  less: "less",
  go: "go",
  rust: "rs",
  java: "java",
  kotlin: "kt kts",
  swift: "swift",
  c: "c h",
  cpp: "cpp cc cxx hpp hh hxx",
  csharp: "cs",
  php: "php phtml",
  perl: "pl pm t",
  ruby: "rb rake gemspec ru",
  jinja: "j2 jinja jinja2",
  html: "html htm",
  xml: "xml xsd xsl svg plist",
  vue: "vue",
  svelte: "svelte",
  astro: "astro",
  graphql: "graphql gql",
  sql: "sql",
  ini: "ini cfg conf",
  diff: "diff patch",
  proto: "proto",
  terraform: "tf tfvars",
  hcl: "hcl",
  nix: "nix",
  nginx: "nginx",
  make: "mk mak",
  just: "just",
  cmake: "cmake",
  lua: "lua",
  r: "r",
  scala: "scala sc",
  groovy: "groovy gradle",
  dart: "dart",
  elixir: "ex exs",
  erlang: "erl hrl",
  haskell: "hs",
  clojure: "clj cljs cljc edn",
  fsharp: "fs fsx fsi",
  "objective-c": "m mm",
  powershell: "ps1 psm1 psd1",
  bat: "bat cmd",
  zig: "zig",
  julia: "jl",
  latex: "tex sty cls",
} satisfies Record<string, string>)) for (const e of exts.split(" ")) EXT[e] = id as LangId;

/** Bare file names that carry no extension. */
const NAME: Record<string, LangId> = {
  Dockerfile: "docker",
  Makefile: "make",
  GNUmakefile: "make",
  makefile: "make",
  justfile: "just",
  Justfile: "just",
  "CMakeLists.txt": "cmake",
  Gemfile: "ruby",
  Rakefile: "ruby",
  Vagrantfile: "ruby",
  Podfile: "ruby",
  Brewfile: "ruby",
  Jenkinsfile: "groovy",
  "nginx.conf": "nginx",
};

/** A file's language by its name, or null when it is not one of the 60. */
export function languageOf(path: string): LangId | null {
  const name = path.split("/").pop() ?? "";
  const dot = name.lastIndexOf(".");
  return own(NAME, name) ?? (dot > 0 ? own(EXT, name.slice(dot + 1).toLowerCase()) : null);
}

/** Fence info strings that are not an id: the usual short names. */
const ALIAS: Record<string, LangId> = {
  ts: "typescript",
  js: "javascript",
  py: "python",
  python3: "python",
  rb: "ruby",
  sh: "shellscript",
  bash: "shellscript",
  zsh: "shellscript",
  shell: "shellscript",
  console: "shellscript",
  yml: "yaml",
  md: "markdown",
  rs: "rust",
  kt: "kotlin",
  cs: "csharp",
  "c#": "csharp",
  "c++": "cpp",
  pl: "perl",
  ps1: "powershell",
  dockerfile: "docker",
  makefile: "make",
  tf: "terraform",
  j2: "jinja",
  jinja2: "jinja",
  gql: "graphql",
  objc: "objective-c",
  "f#": "fsharp",
  hs: "haskell",
  clj: "clojure",
  ex: "elixir",
  erl: "erlang",
  tex: "latex",
  patch: "diff",
  protobuf: "proto",
  golang: "go",
  batch: "bat",
  cmd: "bat",
  htm: "html",
};

/** A fence's info string as a language, or null. */
export function aliasOf(info: string | undefined): LangId | null {
  const k = info?.trim().toLowerCase();
  if (!k) return null;
  return Object.hasOwn(LANGS, k) ? (k as LangId) : own(ALIAS, k);
}

/** What the file tree's icon and `data-lang` group a language under. */
export type Family = "clike" | "python" | "shell" | "yaml" | "markdown" | "plain";

const FAMILY: Partial<Record<LangId, Family>> = {
  python: "python",
  shellscript: "shell",
  docker: "shell",
  make: "shell",
  just: "shell",
  powershell: "shell",
  bat: "shell",
  yaml: "yaml",
  toml: "yaml",
  ini: "yaml",
  markdown: "markdown",
};

export function familyOf(id: LangId | null): Family {
  return id ? FAMILY[id] ?? "clike" : "plain";
}
