import type { Problem, Result } from "../templates/draft/types";

export type Access = "available" | "override" | "never";
export const ACCESS: { value: Access; label: string }[] = [
  { value: "available", label: "Available" },
  { value: "override", label: "Override" },
  { value: "never", label: "Never" },
];
export const ACCESS_WORD: Record<Access, string> = { available: "Available", override: "Override", never: "Never" };

export interface TaskRef {
  chain: string;
  path: string;
  profile: string | null;
  /** Reaches this harness through a `fallback:` entry, not as its first choice. */
  fallback: boolean;
}
export interface HarnessView {
  id: string;
  state: Access;
  executable_found: boolean;
  provider: string | null;
  enabled: boolean;
  executable: string | null;
  defaults: Record<string, string>;
  tasks: TaskRef[];
}
export interface EntryView {
  model?: string;
  effort?: string;
}
export interface ProfileView {
  providers: Record<string, EntryView>;
  effort: string | null;
  tasks: { chain: string; path: string }[];
  used_by_fallback: string[];
}
/** `result.resolved` of the `harnesses` draft (docsite "Harnesses ops"). */
export interface Resolved {
  harnesses: HarnessView[];
  allowed_tools: string[] | null;
  escalation: { harness: string | null; grants: string[] | null };
  escalation_effective: { harness: string; set: boolean };
  profiles: Record<string, ProfileView>;
}
/** A problem as the harnesses draft sends it: the launch ones say which pairing failed. */
export type HProblem = Problem & { chain?: string | null; profile?: string; provider?: string };

export const resolvedOf = (r: Result): Resolved | null => (r.resolved as unknown as Resolved | null) ?? null;
export const ESCALATION_PATH = "defaults.escalation_harness";

const key = (chain: string, path: string) => `${chain}\u0000${path}`;

/** The problems about one task: the same chain and path (a fallback entry's problem keeps its task's path). */
export function problemsOfTask(problems: HProblem[], t: { chain: string; path: string }): HProblem[] {
  return problems.filter((p) => p.chain === t.chain && p.path === t.path);
}

/** What is wrong with a harness: its tasks' problems, and escalation when it runs there. */
export function problemsOfHarness(r: Resolved, problems: HProblem[], id: string): HProblem[] {
  const h = r.harnesses.find((x) => x.id === id);
  if (!h) return [];
  const mine = new Set(h.tasks.map((t) => key(t.chain, t.path)));
  return problems.filter(
    (p) => (p.chain && p.path && mine.has(key(p.chain, p.path))) || (p.path === ESCALATION_PATH && r.escalation_effective.harness === id),
  );
}

/** What is wrong with a profile: a launch problem naming it, or a task that selects it. */
export function problemsOfProfile(r: Resolved, problems: HProblem[], name: string): HProblem[] {
  const tasks = new Set(r.harnesses.flatMap((h) => h.tasks.filter((t) => t.profile === name).map((t) => key(t.chain, t.path))));
  return problems.filter((p) => p.profile === name || (p.chain && p.path && tasks.has(key(p.chain, p.path))));
}

export const providerOf = (r: Resolved, harness: string): string | null => r.harnesses.find((h) => h.id === harness)?.provider ?? null;

export interface Lane {
  key: string;
  title: string;
  icon: "bot" | "layers" | "triangle-alert";
  model?: string;
  effort?: string;
  tag?: string;
  right: string;
  tasks: TaskRef[];
  /** More tasks than the lane draws (floor lanes show five). */
  more: number;
  red: boolean;
  dash: boolean;
  dim: boolean;
  note?: string;
  /** What a click opens (a lane under a harness or profile); none for a lane that only explains. */
  open?: string;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
const FLOOR_TASKS = 5;

/** Nothing selected: one lane per harness, in the file's order. */
export function floorLanes(r: Resolved, problems: HProblem[]): Lane[] {
  return r.harnesses.map((h) => {
    const own = h.tasks.filter((t) => !t.fallback);
    return {
      key: h.id,
      title: h.id,
      icon: "bot",
      tag: ACCESS_WORD[h.state],
      right: `${h.provider ?? "?"} · ${plural(own.length, "task")}`,
      tasks: own.slice(0, FLOOR_TASKS),
      more: Math.max(0, own.length - FLOOR_TASKS),
      red: problemsOfHarness(r, problems, h.id).length > 0,
      dash: false,
      dim: h.state === "never" && own.length === 0,
      open: h.id,
    };
  });
}

/** A harness selected: one lane per profile its tasks select, the entry for its provider on the head. */
export function harnessLanes(r: Resolved, id: string): Lane[] {
  const h = r.harnesses.find((x) => x.id === id);
  if (!h) return [];
  const byProfile = new Map<string | null, TaskRef[]>();
  for (const t of h.tasks) byProfile.set(t.profile, [...(byProfile.get(t.profile) ?? []), t]);
  const names = [...byProfile.keys()].sort((a, b) => (a === null ? 1 : b === null ? -1 : 0));
  return names.map((name) => {
    const tasks = byProfile.get(name)!;
    const entry = name === null ? undefined : r.profiles[name]?.providers[h.provider ?? ""];
    const missing = name !== null && !entry;
    return {
      key: name ?? "none",
      title: name ?? "no profile",
      icon: name === null ? "bot" : "layers",
      model: name === null ? h.defaults.model || "harness default" : (entry?.model ?? "no entry"),
      effort: entry?.effort,
      right: plural(tasks.length, "task"),
      tasks,
      more: 0,
      red: missing,
      dash: name === null,
      dim: false,
      note: missing ? `${name} has no ${h.provider} entry` : undefined,
      open: name ?? undefined,
    };
  });
}

/** A profile selected: one lane per provider entry, and a red lane per provider a task needs it to have. */
export function profileLanes(r: Resolved, name: string): Lane[] {
  const p = r.profiles[name];
  if (!p) return [];
  const landing = new Map<string, TaskRef[]>();
  for (const h of r.harnesses) {
    for (const t of h.tasks.filter((x) => x.profile === name)) {
      const list = landing.get(h.provider ?? "") ?? [];
      if (!list.some((x) => x.chain === t.chain && x.path === t.path)) list.push(t);
      landing.set(h.provider ?? "", list);
    }
  }
  const harnessesOf = (provider: string) => r.harnesses.filter((h) => h.provider === provider).map((h) => h.id).join(", ");
  const lanes: Lane[] = Object.entries(p.providers).map(([provider, e]) => {
    const tasks = landing.get(provider) ?? [];
    return {
      key: provider,
      title: provider,
      icon: "bot",
      model: e.model,
      effort: e.effort,
      right: harnessesOf(provider),
      tasks,
      more: 0,
      red: false,
      dash: false,
      dim: false,
      note: tasks.length ? undefined : `No task runs ${name} on ${provider} yet.`,
      open: provider,
    };
  });
  for (const [provider, tasks] of landing) {
    if (p.providers[provider]) continue;
    lanes.push({
      key: `missing-${provider}`,
      title: `no ${provider} entry`,
      icon: "triangle-alert",
      right: plural(tasks.length, "task"),
      tasks,
      more: 0,
      red: true,
      dash: false,
      dim: false,
      note: `These run on ${provider} and select ${name}. Add a ${provider} entry or change the task.`,
    });
  }
  return lanes;
}

/** The tasks of a harness or profile, as `chain › path` for names and links. */
export const taskName = (t: { chain: string; path: string }) => `${t.chain} › ${t.path}`;
export const taskLeaf = (t: { path: string }) => t.path.split(".").pop() ?? t.path;
