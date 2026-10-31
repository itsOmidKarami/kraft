import { Fragment, type ReactNode } from "react";
import { plural } from "../../format";
import type { KraftEvent } from "../../types";
import { LinkButton } from "../ui/LinkButton";
import { targetedWords } from "./TestsLine";
import type { ItemDetail } from "./useItem";

/** What a stop's `facts.cause` says to do about it, in one place for the desktop's state card and the phone's: a
 *  refused forge credential is fixed on the server's machine (the CLI signed in, or for an API account its token
 *  replaced where its source reads it: `forge_token`), one that only needs single
 *  sign-on is authorized on the forge's website, a stranded claim just needs a
 *  retry, and any other infrastructure stop, or one naming no cause, says where to look. */
export type FailedFix = "forge_login" | "forge_sso" | "forge_token" | "retry" | "repo";

export const causeOf = (item: Pick<ItemDetail, "stop">): unknown => (item.stop?.facts as Record<string, unknown> | undefined)?.cause;

/** `facts.sso_url` when it is an `https://` address of printable ASCII and no backtick (the server's `safe_sso_url` and
 *  the console's `_sso_url` agree; it becomes a link, so nothing else gets through). */
export const SSO_URL = /^https:\/\/[!-_a-~]+$/;
const ssoUrl = (item: Pick<ItemDetail, "stop">): string | null => {
  const url = (item.stop?.facts as Record<string, unknown> | undefined)?.sso_url;
  return typeof url === "string" && SSO_URL.test(url) ? url : null;
};

/** `facts.account`/`source` when both are non-empty text with no backtick and the source is a kind the hints know
 *  (the console's `_token_hint` agrees). */
const tokenHint = (item: Pick<ItemDetail, "stop">): string | null => {
  const f = (item.stop?.facts ?? {}) as Record<string, unknown>;
  const { account, source } = f;
  if (![account, source].every((v) => typeof v === "string" && v !== "" && !v.includes("`"))) return null;
  const [kind, ...rest] = (source as string).split(" ");
  const value = rest.join(" ");
  const hint = Object.hasOwn(FORGE_TOKEN_HINTS, kind) ? FORGE_TOKEN_HINTS[kind] : undefined;
  if (hint === undefined || (kind === "env" || kind === "file") !== (value !== "")) return null;
  // A replacer function: a `$&` or `$'` in a path is not a pattern.
  return hint.replace(/\{(account|value)\}/g, (_m, k: string) => (k === "account" ? (account as string) : value));
};

/** What the failed card offers besides Retry and Escalate (a forge task's failure carries its cause on a `failed` stop too). */
export function failedFix(item: Pick<ItemDetail, "stop">): FailedFix {
  const cause = causeOf(item);
  if (cause === "forge_auth") return ssoUrl(item) ? "forge_sso" : tokenHint(item) ? "forge_token" : "forge_login";
  if (item.stop?.kind !== "infra" || cause === "stranded") return "retry";
  return "repo";
}

/** The fix for `forge_login`: Kraft runs the forge CLI, so the CLI has to be signed in where the server runs. `gh` is
 *  GitHub's, `glab` GitLab's (`kraft.adapters.forge`); the repo's settings hold no token. Backticks mark the commands. */
export const FORGE_LOGIN_HINT =
  "Sign the forge CLI in on the server's machine: `gh auth login`, or `glab auth login` for GitLab. If git itself can't authenticate, check its credentials there too (`gh auth setup-git`, your SSH key or credential helper). Then Retry.";

/** The fix for `forge_sso`: the forge accepted the token but wants it authorized for the organization. The address
 *  comes last, so a long one wraps with nothing after it. */
export const FORGE_SSO_HINT = "Open this address to authorize the forge CLI's token for single sign-on, then Retry: {sso_url}";

/** The fix for `forge_token`, by the kind of source (`TokenSource.label()`'s first word): an API account's token could
 *  not sign in. A variable is read only at server start; a long path comes last. */
export const FORGE_TOKEN_HINTS: Record<string, string> = {
  env: "Set `{value}` to a new token where Kraft starts, then restart Kraft. Then Retry.",
  file: "Put a new token in the file, then check it with `kraft admin forge test {account}`. Then Retry: `{value}`",
  command: "Fix the token command for {account} in forges.yaml, then check it with `kraft admin forge test {account}`. Then Retry.",
  "git-credential": "Save a new token in git's credential helper for this host, then check it with `kraft admin forge test {account}`. Then Retry.",
};

/** The hint under a failed forge stop (`FORGE_LOGIN_HINT`, `FORGE_TOKEN_HINTS` filled or `FORGE_SSO_HINT` with its address), or null for any other. */
export function forgeHint(item: Pick<ItemDetail, "stop">): string | null {
  const fix = failedFix(item);
  if (fix === "forge_token") return tokenHint(item);
  return fix === "forge_sso" ? FORGE_SSO_HINT.replace("{sso_url}", ssoUrl(item) ?? "") : fix === "forge_login" ? FORGE_LOGIN_HINT : null;
}

/** A forge hint as the cards show it: the SSO hint's address is a link, any other hint is `withCode`. */
export function hintText(hint: string): ReactNode {
  const [head] = FORGE_SSO_HINT.split("{sso_url}");
  const url = hint.slice(head.length);
  if (!hint.startsWith(head) || !SSO_URL.test(url)) return withCode(hint);
  return <>{head}<LinkButton inline href={url} target="_blank" rel="noopener noreferrer">{url}</LinkButton></>;
}

/** The words of `text` with each `backticked` run as <code>. */
export function withCode(text: string): ReactNode {
  return text.split("`").map((part, i) => (i % 2 ? <code key={i}>{part}</code> : <Fragment key={i}>{part}</Fragment>));
}

/** "Do I lose anything?": the branch, the files changed on it and whether its tests passed; empty when none is known. */
export function keptLine(item: ItemDetail, fileCount: number | null | undefined): string {
  return [item.branch && `branch ${item.branch}`, fileCount && plural(fileCount, "file"), item.test_result?.passed && `${targetedWords(item.test_result)}tests passing`].filter(Boolean).join(" · ");
}

/** A cause in words, for the ones the card has no fix text for: a refused credential has `FORGE_LOGIN_HINT`, and a
 *  cause not listed here stays off the card as before. */
const CAUSE_WORDS: Record<string, string> = {
  forge_unreachable: "the forge could not be reached",
  stranded: "the run was left behind by a restart",
  ci_infra: "CI itself failed, not the change",
  crash: "the server stopped while it ran",
};

const text = (v: unknown) => (typeof v === "string" || typeof v === "number" ? String(v) : null);

/** A stop's failed test scopes: `facts.failed_scopes` when more than one went red, else the first scope's own keys. */
function failedScopes(facts: Record<string, unknown>): Record<string, unknown>[] {
  if (Array.isArray(facts.failed_scopes)) return facts.failed_scopes.filter((s): s is Record<string, unknown> => !!s && typeof s === "object");
  return text(facts.command) ? [facts] : [];
}

/** One failed scope as the stop reason names it: `npm test` (scope `**`, exit 1). */
function scopeLine(s: Record<string, unknown>): ReactNode {
  const detail = [s.setup ? `setup of area ${text(s.area) ?? ""}`.trim() : text(s.scope) ? `scope ${text(s.scope)}` : null, text(s.exit_code) ? `exit ${text(s.exit_code)}` : null].filter(Boolean);
  return <><code>{text(s.command)}</code>{detail.length > 0 && ` (${detail.join(", ")})`}</>;
}

/** The causes a failed stop carries as structured facts, as readable rows: the failing test scopes, the forge's
 *  cause, and any other plain fact the server sent. `structured` says the reason's own " — tests failed: …" tail
 *  is repeated here, so the card's paragraph can stop at the node. */
export function failureFacts(item: ItemDetail): { rows: [string, ReactNode][]; structured: boolean } {
  const facts = (item.stop?.facts ?? {}) as Record<string, unknown>;
  const scopes = failedScopes(facts);
  const rows: [string, ReactNode][] = [];
  if (scopes.length) rows.push(["tests failed", <ul key="scopes" className="item-card-list">{scopes.map((s, i) => <li key={i}>{scopeLine(s)}</li>)}</ul>]);
  const why = CAUSE_WORDS[text(facts.cause) ?? ""];
  if (why) rows.push(["why", why]);
  // `sso_url` is in the hint, as a link. The scope's own keys (and the list) are the "tests failed" row above, not facts of their own.
  const shown = new Set(["cause", "sso_url", "account", "source", "failed_scopes", ...(scopes.length ? ["command", "scope", "exit_code", "area", "setup", "session_id"] : [])]);
  for (const [k, v] of Object.entries(facts)) if (!shown.has(k) && text(v)) rows.push([k, text(v)!]);
  return { rows, structured: scopes.length > 0 };
}

/** The stop's reason without the " — tests failed: …" tail that `failureFacts` lists as rows. */
export const headline = (reason: string | null | undefined) => (reason ?? "").replace(/ — tests failed:.*$/s, "") || undefined;

/** The events that pause an item; the latest says why a paused one is. */
const PAUSES = new Set(["pause_requested", "paused_by_broken_base", "work_item_blocked_by_dependency"]);

/** Why a paused item paused when no person did: another item's merge broke the base it is built on. The desktop's
 *  paused card and the phone's say it in these words, with who broke it and the follow-up bead as facts. Null for
 *  any other pause. */
export const BROKEN_BASE_TEXT = "Another item's merge broke the base this one is built on, so Kraft paused it. Resume it once the base is fixed.";
export function brokenBase(events: KraftEvent[]): { by: string; bead: string | null } | null {
  const last = [...events].reverse().find((e) => PAUSES.has(e.type));
  if (last?.type !== "paused_by_broken_base") return null;
  const bead = last.payload.follow_up_bead;
  return { by: String(last.payload.broken_by), bead: typeof bead === "string" ? bead : null };
}
