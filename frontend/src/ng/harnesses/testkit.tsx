import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { HeaderActionsHost, HeaderTailHost } from "../shell/HeaderActions";
import { vi } from "vitest";
import type { DraftView, Problem } from "../templates/draft/types";
import type { Access, HarnessView, ProfileView, Resolved, TaskRef } from "./model";
import { resetProviders } from "./useProviders";
import { HarnessesPage } from "./HarnessesPage";

const task = (path: string, profile: string | null, fallback = false): TaskRef => ({ chain: "default", path, profile, fallback });

export const TASKS = {
  implementer: task("implement.main.implementer", "strong"),
  repair: task("implement.fix.repair_verification", "strong"),
  spec: task("spec.main.spec_author", null),
  review: task("review.main.code_review", null),
  summary: task("finish.main.write_summary", "fast"),
};

const harness = (id: string, provider: string, state: Access, tasks: TaskRef[], defaults: Record<string, string> = {}): HarnessView => ({
  id, provider, state, tasks, defaults, enabled: true, executable: id === "claude-sandbox" ? "claude --sandbox" : null, executable_found: id !== "gemini",
});

/** A resolved answer shaped like the harnesses draft's (docsite "Harnesses ops"). */
export function resolved(over: Partial<Resolved> & { access?: Record<string, Access> } = {}): Resolved {
  const access = { claude: "available", "claude-sandbox": "override", codex: "available", cursor: "never", gemini: "never", ...over.access } as Record<string, Access>;
  const profiles: Record<string, ProfileView> = over.profiles ?? {
    strong: { providers: { claude: { model: "sonnet", effort: "high" }, codex: { model: "gpt-5.6-terra", effort: "high" } }, effort: "high", tasks: [TASKS.implementer, TASKS.repair], used_by_fallback: [] },
    fast: { providers: { claude: { model: "haiku", effort: "low" } }, effort: "low", tasks: [TASKS.summary], used_by_fallback: [] },
    deep: { providers: { claude: { model: "opus", effort: "high" } }, effort: "high", tasks: [], used_by_fallback: [] },
  };
  return {
    harnesses: [
      harness("claude", "claude", access.claude, [TASKS.implementer, TASKS.repair, TASKS.spec, TASKS.review], { model: "sonnet", permission_mode: "acceptEdits" }),
      harness("claude-sandbox", "claude", access["claude-sandbox"], []),
      harness("codex", "codex", access.codex, [TASKS.summary], { effort: "medium" }),
      harness("cursor", "cursor", access.cursor, []),
      harness("gemini", "gemini", access.gemini, []),
    ],
    allowed_tools: ["git", "shell", "editor"],
    escalation: { harness: "claude", grants: ["git-commit", "git-rebase"] },
    escalation_effective: { harness: "claude", set: true },
    profiles,
    ...(over.harnesses ? { harnesses: over.harnesses } : {}),
    ...(over.allowed_tools !== undefined ? { allowed_tools: over.allowed_tools } : {}),
    ...(over.escalation ? { escalation: over.escalation } : {}),
    ...(over.escalation_effective ? { escalation_effective: over.escalation_effective } : {}),
  };
}

export const problem = (p: Partial<Problem> & { chain?: string | null; profile?: string; provider?: string }): Problem =>
  ({ path: "", field: null, message: "", file: "harnesses.yaml", line: 1, col: 1, ...p }) as Problem;

export const NO_ENTRY = problem({ chain: "default", path: TASKS.summary.path, profile: "fast", provider: "codex", message: `${TASKS.summary.path}: profile 'fast' has no codex entry` });
export const NEVER = problem({ chain: "default", path: TASKS.implementer.path, file: "chains/default.yaml", message: `${TASKS.implementer.path}: harness 'claude' is not in its allowed_harnesses ['codex']` });
export const ESCALATION = problem({ path: "defaults.escalation_harness", field: "escalation_harness", file: "policy.yaml", message: "escalation runs on 'claude', which is set to Never" });

export function view(r: Resolved, opts: { problems?: Problem[]; changes?: { path: string; kind: "add" | "change" | "remove"; summary: string }[]; draft?: boolean; files?: Record<string, string>; published?: Record<string, string | null> } = {}): DraftView {
  return {
    area: "harnesses", key: "harnesses", draft: opts.draft ?? (opts.changes?.length ?? 0) > 0,
    files: opts.files ?? { "harnesses.yaml": "harnesses: {}\n", "policy.yaml": "defaults: {}\n" }, base: {}, updated_at: null, ...(opts.published ? { published: opts.published } : {}),
    result: {
      model: {}, resolved: r as never, problems: opts.problems ?? [], sources: {}, changes: opts.changes ?? [], impact: {}, warnings: [],
      policy_values: { auto_escalate_delay_s: 0, auto_review_attempts: 0 },
      choices: { ref: [], target: [], inputs: [], grants: GRANTS },
    },
  };
}

/** The grants the server lists in a draft's `choices`. */
export const GRANTS = [{ value: "git-commit", summary: "A plain git commit" }, { value: "git-rebase", summary: "A plain git rebase" }, { value: "git-push", summary: "A plain git push to the item's own branch" }];

export const STATUS = [
  { id: "claude", executable: "claude", executable_found: true, efforts: ["low", "medium", "high", "xhigh", "max"], models: ["sonnet", "opus", "haiku"], capabilities: { effort: { cli: ["--effort", "{value}"], values: [] }, permission_mode: { cli: ["--permission-mode", "{value}"], values: ["default", "acceptEdits", "plan"] } } },
  { id: "codex", executable: "codex", executable_found: true, efforts: ["minimal", "low", "medium", "high"], models: [], capabilities: {} },
  { id: "cursor", executable: "cursor-agent", executable_found: true, efforts: [], models: [], capabilities: {} },
  { id: "gemini", executable: "gemini", executable_found: false, efforts: [], models: [], capabilities: {} },
];

export interface Server {
  ops: Record<string, unknown>[][];
  calls: string[];
  setView: (v: DraftView) => void;
}

/** A fetch that answers the draft, the provider status and the published policy; each `ops` post is recorded. */
export function serve(first: DraftView, opts: { published?: unknown; publishAnswer?: () => Response; opsAnswer?: (ops: Record<string, unknown>[]) => Response | null } = {}): Server {
  let current = first;
  const server: Server = { ops: [], calls: [], setView: (v) => void (current = v) };
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const path = String(url).replace(/^.*\/api/, "");
    server.calls.push(`${init?.method ?? "GET"} ${path}`);
    if (path === "/drafts/harnesses/harnesses" && !init?.method) return Promise.resolve(json(current));
    if (path.startsWith("/drafts/harnesses/harnesses/ops")) {
      const ops = JSON.parse(String(init?.body)).ops as Record<string, unknown>[];
      server.ops.push(ops);
      return Promise.resolve(opts.opsAnswer?.(ops) ?? json({ ...current, ops: ops.map((o) => ({ op: o.op })) }));
    }
    if (path === "/drafts/harnesses/harnesses/publish") return Promise.resolve(opts.publishAnswer?.() ?? json({ published: ["harnesses.yaml", "policy.yaml"], result: current.result }));
    if (path === "/harnesses") return Promise.resolve(json(STATUS));
    if (path === "/policy") return Promise.resolve(json(opts.published ?? { maxima: { allowed_tools: ["git", "shell"] }, defaults: { escalation_grants: ["git-commit"] } }));
    if (path === "/drafts") return Promise.resolve(json([]));
    return Promise.reject(new TypeError(`no route ${path}`));
  });
  return server;
}

/** Renders the page with a header-tail host, so the draft state and problem badge can be read from `tail`. */
export function renderPage(query = "") {
  resetProviders();
  const tail = document.createElement("div");
  tail.setAttribute("data-testid", "header-tail");
  const actions = document.createElement("div");
  document.body.append(tail, actions);
  const out = render(
    <HeaderTailHost.Provider value={tail}>
      <HeaderActionsHost.Provider value={actions}>
        <MemoryRouter initialEntries={[`/settings/harnesses${query}`]}>
          <HarnessesPage />
        </MemoryRouter>
      </HeaderActionsHost.Provider>
    </HeaderTailHost.Provider>,
  );
  return { ...out, tail };
}

/** The floor's pane starts on its rail (AreaHarnesses paneOpen:false): expand it and return it. */
export async function openAreaPane() {
  await userEvent.click(await screen.findByRole("button", { name: "Expand harnesses" }));
  return screen.getByRole("complementary", { name: "harnesses pane" });
}
