import { Fragment, type ReactNode } from "react";
import { plural } from "../../format";
import type { ItemDetail } from "./useItem";

/** What a stop's `facts.cause` says to do about it, in one place for the desktop's state card and the phone's: a
 *  refused forge credential is fixed on the server's machine (Kraft holds no token), a stranded claim just needs a
 *  retry, and any other infrastructure stop, or one naming no cause, says where to look. */
export type FailedFix = "forge_login" | "retry" | "repo";

export const causeOf = (item: ItemDetail): unknown => (item.stop?.facts as Record<string, unknown> | undefined)?.cause;

/** What the failed card offers besides Retry and Escalate (a forge task's failure carries its cause on a `failed` stop too). */
export function failedFix(item: ItemDetail): FailedFix {
  const cause = causeOf(item);
  if (cause === "forge_auth") return "forge_login";
  if (item.stop?.kind !== "infra" || cause === "stranded") return "retry";
  return "repo";
}

/** The fix for `forge_login`: Kraft runs the forge CLI, so the CLI has to be signed in where the server runs. `gh` is
 *  GitHub's, `glab` GitLab's (`kraft.adapters.forge`); the repo's settings hold no token. Backticks mark the commands. */
export const FORGE_LOGIN_HINT = "Sign the forge CLI in on the server's machine: `gh auth login`, or `glab auth login` for GitLab. Then Retry.";

/** The words of `text` with each `backticked` run as <code>. */
export function withCode(text: string): ReactNode {
  return text.split("`").map((part, i) => (i % 2 ? <code key={i}>{part}</code> : <Fragment key={i}>{part}</Fragment>));
}

/** "Do I lose anything?": the branch, the files changed on it and whether its tests passed; empty when none is known. */
export function keptLine(item: ItemDetail, fileCount: number | null | undefined): string {
  return [item.branch && `branch ${item.branch}`, fileCount && plural(fileCount, "file"), item.test_result?.passed && "tests passing"].filter(Boolean).join(" · ");
}
