import { plural } from "../../format";
import type { ForgeAccountRow, Repo } from "../../types";
import { SSO_URL } from "../item/cause";
import type { Tone } from "../ui/tone";

export type ForgeAccount = ForgeAccountRow & { name: string };

export const accountsOf = (body: { accounts: Record<string, ForgeAccountRow> }): ForgeAccount[] =>
  Object.entries(body.accounts ?? {}).map(([name, row]) => ({ name, ...row }));

/** Days before `expires_on` at which a token reads as a warning: `probe.py`'s `EXPIRY_WARN_DAYS`, where `/health` turns
 *  degraded at the same bound (a test keeps the two equal). */
export const EXPIRY_WARN_DAYS = 7;

export const FORGES_LEDE = "Which forge each host runs and, for an API account, where Kraft reads its token and what its last check found.";
export const EMPTY_FORGES = "No forge accounts. Kraft uses gh and glab as they are signed in, as it always has.";
export const EMPTY_FORGES_WHY = "Add one for a self-hosted host whose name does not say its forge, or to sign in with a token instead of gh or glab.";
export const CLI_SIGN_IN = (forge: string) => (forge === "github" ? "gh's own sign-in" : "glab's own sign-in");
export const COMMAND_READ_ONLY = (name: string) => `A command runs on the server's machine, so it is set there: kraft admin forge edit ${name} --token-from 'command:…'`;
export const COMMAND_ADD_HINT = "A token command is set on the server's machine: kraft admin forge add NAME --token-from 'command:…'";
export const ENV_RESTART = "Kraft takes the variable from its own environment when it starts, so a new value needs a restart.";
export const PASTE_ONCE = "Kraft keeps it in a file under its home that only the server's user can read, and never shows it again.";
export const TO_CLI_DROPS = "With client cli, gh or glab keep their own sign-in: the token source and CA file are dropped, and a pasted token's file is deleted.";
export const NAMED_BY = (repos: string[]) => `Named by ${repos.join(", ")}: give them another forge account first.`;
export const DEFAULT_REMOVABLE = "Repos that use it as their host's default go back to gh or glab.";
export const FIXED_FIELDS = "To change these, remove the account and add it again.";
export const PHONE_READ_ONLY = "Add accounts and change their addresses in Settings on a wider screen, or with kraft admin forge in a terminal.";
export const PHONE_REMOVE = "It goes at once, and a pasted token's file is deleted with it.";

export function sourceWords(a: ForgeAccount): string {
  if (a.client === "cli") return CLI_SIGN_IN(a.forge);
  const t = a.token;
  if (t?.from === "env") return `variable ${t.env}`;
  if (t?.from === "file") return `file ${t.file}`;
  if (t?.from === "command") return "command";
  return "git credential helper";
}

const BAD: Record<string, string> = { no_token: "no token", refused: "refused", forbidden: "forbidden", tls: "TLS failed" };
const safeSso = (u: string | undefined): string | null => (u && SSO_URL.test(u) ? u : null);

/** The state a person must act on first (first match wins). Days left come from the date's y/m/d parts against the
 *  local day: `new Date("YYYY-MM-DD")` is UTC midnight and would be a day off late in the local day. */
export function probeWords(a: ForgeAccount, today: Date): { word: string | null; tone: Tone; line: string; ssoUrl: string | null } {
  const out = (word: string | null, tone: Tone, line = "", ssoUrl: string | null = null) => ({ word, tone, line, ssoUrl });
  const p = a.probe;
  if (a.client === "cli") return out(null, "muted", CLI_SIGN_IN(a.forge));
  if (!p) return out("not checked yet", "muted");
  // An address that fails the check still says "needs SSO", but as plain text, never a link.
  if (p.state === "forbidden" && p.sso_url) return out("needs SSO", "bad", p.sso_url, safeSso(p.sso_url));
  if (BAD[p.state]) return out(BAD[p.state], "bad", p.detail);
  if (p.state === "unreachable") return out("unreachable", "muted", p.detail);
  if (p.expires_on) {
    const [y, m, d] = p.expires_on.split("-").map(Number);
    const left = Math.round((Date.UTC(y, m - 1, d) - Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())) / 86_400_000);
    if (left < 0) return out("expired", "bad");
    if (left <= EXPIRY_WARN_DAYS) return out(left === 0 ? "expires today" : `expires in ${plural(left, "day")}`, "warn");
  }
  const hidden = Object.values(p.projects).filter((x) => !x.visible);
  if (hidden.length) {
    const link = hidden.map((x) => safeSso(x.sso_url)).find(Boolean) ?? null;
    return out(`cannot see ${plural(hidden.length, "repo")}`, "warn", `cannot see ${hidden.map((x) => x.project).join(", ")}`, link);
  }
  return out("signed in", "ok", `as ${p.login}`);
}

export const namedBy = (repos: Pick<Repo, "name" | "forge_account">[], name: string): string[] =>
  repos.filter((r) => r.forge_account === name).map((r) => r.name);

export const SWITCH_DELETES_PASTED = "If this file is a pasted token, switching to another source deletes it.";
