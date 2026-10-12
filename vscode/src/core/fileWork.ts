/** Filing a work item from VS Code: the title rules, the request body, which
 *  connected repo and chains apply, and when filing is refused. Pure: the
 *  Quick Input steps in `../fileWork.ts` ask these. */

import { basename } from "node:path";
import { humanSize } from "../../../frontend/src/sizes";
import type { Created, DryRun, NewWorkItem, Repo, TemplateSummary } from "./api";
import { isLoopback } from "./connection";
import { refusal, type ReadOnlyState } from "./readOnly";
import { samePath } from "./repoScope";

export const MAX_TITLE = 500; // src/kraft/adapters/beads.py MAX_TITLE

/** Every character `str.splitlines` breaks at (`_LINE_BREAKS`, api/routes/work_items.py). */
const LINE_BREAK = /[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/;
const LINE_BREAKS = new RegExp(LINE_BREAK.source, "g");
/** What the server refuses in a title (`_check_one_line`): category Cc (C0,
 *  DEL, C1), a lone surrogate (Cs), and the bidi controls. */
const CONTROL = /[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]|[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/;
const CONTROLS = new RegExp(CONTROL.source, "g");

export interface FileDraft {
  title: string; description: string; repo: string; chain?: string; spec?: string; plan?: string;
  /** The first attachment's git toplevel, so a document in a linked worktree resolves. */
  cwd?: string;
}

/** A title from a selection: its first non-blank line as plain text, cut at a
 *  code point (Python's `len`), never inside a surrogate pair. */
export function titleFrom(text: string): string {
  const line = text.split(LINE_BREAKS).find((l) => /\S/.test(l)) ?? "";
  const plain = line.replace(CONTROLS, " ").trim();
  return [...plain].slice(0, MAX_TITLE).join("").trimEnd();
}

/** Why the server would refuse this title, in the order it checks (`_check_title`). */
export function titleProblem(title: string): string | undefined {
  if (!title.trim()) return "A work item needs a title.";
  if (LINE_BREAK.test(title)) return "The title is one line: put the rest in the description.";
  if (CONTROL.test(title)) return "The title is plain text: no tab or other control character.";
  const n = [...title].length;
  if (n > MAX_TITLE) return `The title is ${n} characters; the limit is ${MAX_TITLE}.`;
  return undefined;
}

/** The preview and the create send this one body. `cwd` only widens where an
 *  attachment may be, so it goes only with one (`client/actions.py`). */
export function createBody(d: FileDraft, autostart: boolean): NewWorkItem {
  const attachments = (["spec", "plan"] as const).filter((k) => d[k]).map((kind) => ({ kind, path: d[kind]! }));
  return {
    title: d.title.trim(),
    description: d.description.trim(),
    repo: d.repo,
    ...(d.chain ? { chain: d.chain } : {}),
    ...(attachments.length ? { attachments, ...(d.cwd ? { cwd: d.cwd } : {}) } : {}),
    autostart,
  };
}

/** The connected entry the folder's roots name, as `resolve_repo` would find
 *  it: an auto-connected child (`managed: false`) is walked past to its
 *  superproject. `repo` is the entry's own path, never the folder. */
export function pickRepo(roots: string[], repos: Repo[], platform: NodeJS.Platform): { repo: Repo } | { refusal: string } {
  for (const root of roots) {
    const repo = repos.find((r) => r.managed !== false && samePath(r.path, root, platform));
    if (!repo) continue;
    if (repo.enabled === false) return { refusal: `${repo.path} is disabled in Kraft. Enable it in Settings › Repos first.` };
    return { repo };
  }
  const root = roots.at(-1) ?? "This folder";
  return { refusal: `${root} is not a repo Kraft has connected. Connect this repo first: kraft repo connect ${root}` };
}

const REPO_LIBRARY = "library@repo:";

/** The chains an item in `repo` can run: the instance's own, add-ons', and
 *  this repo's own library (`addons/load.py` `repo_library_id`); never another
 *  repo's library or a chain that does not resolve. */
export function chainsFor(chains: TemplateSummary[], repo: Repo): TemplateSummary[] {
  return chains.filter((c) => {
    if (c.error) return false;
    const addon = c.addon?.id;
    return !addon?.startsWith(REPO_LIBRARY) || (!!repo.id && addon === `${REPO_LIBRARY}${repo.id}`);
  });
}

/** The chain "Repo default" runs, as `deps.chain_template_for` picks it: the
 *  repo's `default_chain` unless unset or `default`, else its own library's
 *  `<id>:default`, else `default`. */
export function repoDefaultChain(repo: Repo, chains: TemplateSummary[]): string {
  if (repo.default_chain && repo.default_chain !== "default") return repo.default_chain;
  const own = repo.id ? `${repo.id}:default` : undefined;
  return own && chains.some((c) => c.id === own) ? own : "default";
}

/** D23: filing reads attachments off the daemon's disk, so Kraft must run on
 *  the machine holding the window's files. A remote window whose extension
 *  host talks to a loopback daemon is that machine. */
export function localRefusal(base: string): string | null {
  if (isLoopback(base)) return null;
  let host = base;
  try {
    host = new URL(base).hostname || base;
  } catch {
    // Not a URL: name it as written.
  }
  return `Filing work needs Kraft on the machine with this window's files, and kraft.url points at ${host}. File it from the web UI instead.`;
}

/** Restricted Mode and a refused server version first, as every action
 *  says them; then the local-only rule. */
export function fileRefusal(state: ReadOnlyState, base: string): string | null {
  return refusal(state) ?? localRefusal(base);
}

export type AttachState = "ok" | "save" | { refusal: string };

/** Whether a document can be attached as it is. Its scheme must be the
 *  workspace folder's (`file`, or `vscode-remote` in a remote window): that is
 *  a file on the machine the daemon reads. A dirty one is saved first. */
export function attachState(doc: { scheme: string; isUntitled?: boolean; isDirty?: boolean }, folderScheme: string): AttachState {
  if (doc.isUntitled || doc.scheme === "untitled") return { refusal: "Save this file into the repo first: an unsaved file cannot be attached." };
  if (doc.scheme !== folderScheme) return { refusal: "Only a file on disk can be attached." };
  return doc.isDirty ? "save" : "ok";
}

export interface AttachChoice { label: string; spec?: string; plan?: string; pick?: true }

/** The attach step's choices: the active Markdown file as spec or plan, when there is one. */
export function attachChoices(activeMd: string | undefined): AttachChoice[] {
  const name = activeMd && basename(activeMd);
  return [
    { label: "No documents" },
    ...(activeMd ? [{ label: `${name} as spec`, spec: activeMd }, { label: `${name} as plan`, plan: activeMd }] : []),
    { label: "Pick files…", pick: true as const },
  ];
}

/** The preview step's title: the chain, how much of it runs, and what the attachments cover. */
export function previewLine(chainLabel: string, dry: DryRun): string {
  const { nodes, skipped, gates } = dry;
  const line = `${chainLabel} · ${nodes.length} of ${nodes.length + skipped.length} nodes run · ${gates.length} gate${gates.length === 1 ? "" : "s"}`;
  if (!skipped.length) return line;
  return `${line} · skips ${skipped.map((s) => (s.why === "covered_by" && s.kind ? `${s.node} (${s.kind} attached)` : s.node)).join(", ")}`;
}

/** What the create answered, said once (the web Composer's `sayIfFiledPaused`).
 *  VS Code sends no `depends_on`, so an autostart is never blocked, and a lost
 *  slot race answers `queued`, never `paused`. */
export function outcomeMessage(autostart: boolean, c: Created): string {
  if (!autostart) return `Filed ${c.id} paused. Nothing spends tokens until you start it.`;
  if (c.status === "active") return `Filed ${c.id} and started it.`;
  if (c.status === "queued") {
    const held = c.slots?.storage;
    if (held) return `Filed ${c.id}, queued: worktrees use ${humanSize(held.used_bytes)} of the ${humanSize(held.limit_bytes)} storage limit. It starts when space frees.`;
    const busy = c.slots ? `${c.slots.busy} of ${c.slots.limit} slots are` : "every slot is";
    return `Filed ${c.id}, queued: ${busy} busy. It starts when one frees.`;
  }
  return `Filed ${c.id}: it is ${c.status}.`;
}

/** The warnings a create came back with, in the server's order of importance. */
export function warningsOf(c: Created): string[] {
  return [c.duplicate_warning, c.bead_warning, c.repo_warning].filter((w): w is string => !!w);
}

/** The warning's buttons. Abandon is offered unless the item is already
 *  running (the server refuses it then); whether it still may is the
 *  server's answer when it is clicked (D24). */
export function warningActions(c: Created): ("Open item" | "Abandon")[] {
  return c.status === "active" ? ["Open item"] : ["Open item", "Abandon"];
}

export const MAX_URL = 8000;

function inside(path: string, root: string): boolean {
  const n = (p: string) => p.replace(/\\/g, "/").replace(/\/+$/, "");
  const [a, b] = [n(path), n(root)];
  const win = /^[A-Za-z]:/.test(b);
  const [x, y] = win ? [a.toLowerCase(), b.toLowerCase()] : [a, b];
  return x === y || x.startsWith(`${y}/`);
}

export interface DraftLink { url: string; briefDropped: boolean; outside: string[] }

/** "More options…": the web draft page (`DraftItemPage` `loadDraft`) with
 *  this draft in its query. That page sends no `cwd`, so a document outside
 *  the repo (another worktree's) is left out; a brief too long for a link is too. */
export function draftUrl(base: string, d: Omit<FileDraft, "cwd">): DraftLink {
  const outside = (["spec", "plan"] as const).map((k) => d[k]).filter((p): p is string => !!p && !inside(p, d.repo));
  const build = (brief: boolean) => {
    const q = new URLSearchParams({ title: d.title });
    if (brief && d.description) q.set("brief", d.description);
    q.set("repo", d.repo);
    if (d.chain) q.set("chain", d.chain);
    for (const k of ["spec", "plan"] as const) if (d[k] && !outside.includes(d[k]!)) q.set(k, d[k]!);
    return `${base}/work-items/new?${q}`;
  };
  const url = build(true);
  if (url.length <= MAX_URL) return { url, briefDropped: false, outside };
  return { url: build(false), briefDropped: true, outside };
}

/** What "More options…" says about what the link could not carry, if anything. */
export function draftNote(link: DraftLink, repo: string): string | undefined {
  const notes = [
    ...(link.briefDropped ? ["The description is too long for a link: it is on your clipboard. Paste it into Brief."] : []),
    ...(link.outside.length
      ? [`${link.outside.map((p) => basename(p)).join(" and ")} ${link.outside.length === 1 ? "is" : "are"} outside ${repo}, which the web page cannot attach: it is left out.`]
      : []),
  ];
  return notes.length ? notes.join(" ") : undefined;
}
