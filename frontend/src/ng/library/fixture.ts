import type { DraftView, Result } from "../templates/draft/types";
import type { PublishedComponent, PublishedLibrary } from "./types";

/** A small library draft, shaped as `GET /drafts/library/library` answers (resolved null, sources {}). Tests only. */
export const LIB_FILE = "library.yaml";

export const LIB_MODEL = {
  steering: { "project-standards": { instructions: "Keep changes focused." }, "never-signal-processes-you-didnt-start": { instructions: "Never signal a process you did not start." } },
  tasks: {
    implementer: { kind: "agent", prompt: "Implement the plan." },
    code_review: { extends: "implementer", prompt: "Review it." },
    verify: { kind: "subprocess", command: "make check" },
    await_ci: { kind: "forge", target: "mr.ci" },
  },
  steps: { checks: { tasks: [{ id: "lint", extends: "verify" }] } },
  nodes: {
    verification: { kind: "exec", steps: [{ id: "review", tasks: [{ id: "code_review", extends: "code_review" }] }] },
    approval: { kind: "gate", message: "Approve." },
  },
};

const RESULT: Result = {
  model: { [LIB_FILE]: LIB_MODEL },
  resolved: null,
  problems: [],
  sources: {},
  changes: [],
  impact: { chains: [], repos: [] },
  warnings: [],
  policy_values: { auto_escalate_delay_s: 0, auto_review_attempts: 1 },
};

export const libView = (extra: Partial<Result> = {}, draft = false): DraftView => ({
  area: "library",
  key: "library",
  draft,
  files: { [LIB_FILE]: "tasks: {}\n" },
  base: { [LIB_FILE]: "0".repeat(64) },
  updated_at: null,
  result: { ...RESULT, ...extra },
});

const comp = (id: string, used_by: string[] = [], paths?: PublishedComponent["used_by_paths"]): PublishedComponent => {
  const [kind, name] = id.split(".");
  return { id, kind: kind as PublishedComponent["kind"], name, plugin: null, used_by, used_by_paths: paths ?? used_by.map((chain) => ({ chain, path: "x", overrides: false })) };
};

export const PUBLISHED: PublishedLibrary = {
  file: "templates/library.yaml",
  text: "tasks: {}\n",
  components: [
    comp("steering.project-standards", ["default"], [{ chain: "default", path: "implementation.main.implement", overrides: false }, { chain: "quick-task", path: "build.main.go", overrides: false }]),
    comp("tasks.implementer", ["default", "quick-task"], [
      { chain: "default", path: "implementation.main.implement", overrides: true },
      { chain: "quick-task", path: "build.main.go", overrides: false, via: "build" },
    ]),
    comp("tasks.code_review", ["default"]),
    comp("tasks.verify"),
    comp("tasks.await_ci", ["default"]),
    comp("steps.checks", ["default"]),
    comp("nodes.verification", ["default"]),
  ],
};
