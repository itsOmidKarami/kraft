// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { forgesFixture } from "../../../e2e/contract/mocks/ngForges";
import type { ForgeProbe } from "../../types";
import { CLI_SIGN_IN, EXPIRY_WARN_DAYS, NAMED_BY, accountsOf, namedBy, probeWords, sourceWords, type ForgeAccount } from "./forgeWords";

const NOW = new Date(2026, 9, 31, 12, 0);
const ymd = (today: Date, days: number) => {
  const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const acct = (over: Partial<ForgeAccount> = {}): ForgeAccount => ({
  name: "x", forge: "gitlab", client: "api", web_url: "https://g.example", api_url: "https://g.example/api/v4",
  default_for_host: false, ca_file: null, token: { from: "git-credential", kind: "unknown" }, probe: null, ...over,
});
const probe = (over: Partial<ForgeProbe> = {}): ForgeProbe => ({
  account: "x", checked_at: NOW.toISOString(), state: "ok", detail: "", login: "alice", scopes: null, expires_on: null,
  warnings: [], sso_url: "", projects: {}, ...over,
});
const SSO = "https://gitlab.example/sso?x=1";

describe("sourceWords says where the token comes from, never a token", () => {
  it.each([
    ["git-credential", { token: { from: "git-credential", kind: "unknown" } }, "git credential helper"],
    ["env", { token: { from: "env", kind: "pat", env: "OLD_GL_TOKEN" } }, "variable OLD_GL_TOKEN"],
    ["file", { token: { from: "file", kind: "pat", file: "/s/tok" } }, "file /s/tok"],
    ["command", { token: { from: "command", kind: "pat", command: "op read secret" } }, "command"],
    ["cli-github", { client: "cli", forge: "github", token: null }, CLI_SIGN_IN("github")],
    ["cli-gitlab", { client: "cli", forge: "gitlab", token: null }, CLI_SIGN_IN("gitlab")],
    ["api-null-token", { token: null }, "git credential helper"],
  ] as [string, Partial<ForgeAccount>, string][])("%s", (_id, over, want) => {
    expect(sourceWords(acct(over))).toBe(want);
  });
});

describe("probeWords puts the state a person must act on first", () => {
  const cases: [string, Partial<ForgeAccount>, Date, Partial<ReturnType<typeof probeWords>>][] = [
    ["cli", { client: "cli", token: null }, NOW, { word: null, line: CLI_SIGN_IN("gitlab") }],
    ["not-checked", {}, NOW, { word: "not checked yet", tone: "muted" }],
    ["ok", { probe: probe() }, NOW, { word: "signed in", tone: "ok", line: "as alice" }],
    ["expires-in-3-days", { probe: probe({ expires_on: ymd(NOW, 3) }) }, NOW, { word: "expires in 3 days", tone: "warn" }],
    ["expires-in-7-days", { probe: probe({ expires_on: ymd(NOW, 7) }) }, NOW, { word: "expires in 7 days", tone: "warn" }],
    ["expires-today", { probe: probe({ expires_on: ymd(NOW, 0) }) }, NOW, { word: "expires today", tone: "warn" }],
    // The local day's end: a UTC-midnight parse of the date would round this the wrong way.
    ["expires-today-late", { probe: probe({ expires_on: ymd(NOW, 0) }) }, new Date(2026, 9, 31, 23, 59), { word: "expires today", tone: "warn" }],
    ["expires-in-8-days", { probe: probe({ expires_on: ymd(NOW, 8) }) }, NOW, { word: "signed in", tone: "ok" }],
    ["expired", { probe: probe({ expires_on: ymd(NOW, -1) }) }, NOW, { word: "expired", tone: "bad" }],
    ["refused", { probe: probe({ state: "refused", detail: "401 nope" }) }, NOW, { word: "refused", tone: "bad", line: "401 nope" }],
    ["no-token", { probe: probe({ state: "no_token", detail: "none" }) }, NOW, { word: "no token", tone: "bad" }],
    ["tls", { probe: probe({ state: "tls", detail: "bad cert" }) }, NOW, { word: "TLS failed", tone: "bad" }],
    ["forbidden-with-sso", { probe: probe({ state: "forbidden", sso_url: SSO }) }, NOW, { word: "needs SSO", tone: "bad", ssoUrl: SSO }],
    ["forbidden-and-hidden-project", { probe: probe({ state: "forbidden", sso_url: SSO, projects: { a: { project: "t/a", visible: false, detail: "" } } }) }, NOW, { word: "needs SSO", tone: "bad" }],
    ["forbidden-no-sso", { probe: probe({ state: "forbidden", detail: "403", sso_url: "" }) }, NOW, { word: "forbidden", tone: "bad", line: "403", ssoUrl: null }],
    ["sso-url-unsafe", { probe: probe({ state: "forbidden", sso_url: "javascript:alert(1)" }) }, NOW, { word: "needs SSO", ssoUrl: null }],
    ["project-sso", { probe: probe({ projects: { a: { project: "t/a", visible: false, detail: "", sso_url: SSO } } }) }, NOW, { ssoUrl: SSO }],
    ["unreachable", { probe: probe({ state: "unreachable", detail: "timed out" }) }, NOW, { word: "unreachable", tone: "muted", line: "timed out" }],
    ["ok-no-login", { probe: probe({ login: null, detail: "token has no user" }) }, NOW, { word: "signed in", tone: "ok", line: "token has no user" }],
    ["cannot-see-unnamed-project", { probe: probe({ detail: "scope", projects: { a: { project: "", visible: false, detail: "" } } }) }, NOW, { word: "cannot see 1 repo", line: "scope" }],
    ["cannot-see-a-project", { probe: probe({ projects: { a: { project: "team/app", visible: false, detail: "" } } }) }, NOW, { word: "cannot see 1 repo", tone: "warn", line: "cannot see team/app" }],
  ];
  it.each(cases)("%s", (_id, over, today, want) => {
    expect(probeWords(acct(over), today)).toMatchObject(want);
  });
});

it("EXPIRY_WARN_DAYS is probe.py's", () => {
  const py = readFileSync(new URL("../../../../src/kraft/adapters/forge/probe.py", import.meta.url), "utf-8");
  expect(Number(/EXPIRY_WARN_DAYS = (\d+)/.exec(py)?.[1])).toBe(EXPIRY_WARN_DAYS);
});

it.each([
  ["one", ["kraft-plugins"], "Can't remove: kraft-plugins uses it. Point it at another account first."],
  ["many", ["a", "b"], "Can't remove: a, b use it. Point them at another account first."],
])("the remove refusal says it is one: %s", (_id, repos, want) => {
  expect(NAMED_BY(repos)).toBe(want);
});

it("namedBy is the repos whose forge_account is the account", () => {
  expect(namedBy([{ name: "a", forge_account: "x" }, { name: "b", forge_account: null }], "x")).toEqual(["a"]);
});

it("the fixture has one account per state", () => {
  const rows = accountsOf({ accounts: forgesFixture(NOW) });
  const words = Object.fromEntries(rows.map((a) => [a.name.slice(0, 12), probeWords(a, NOW).word]));
  expect(words).toMatchObject({
    "github-cli": null, "corp-gl": "signed in", "old-gl": "refused", "soon-gl": "expires in 3 days", "acme-gh": "needs SSO",
    "vault-gl": "signed in", "shared-gl": null, "fresh-gl": "not checked yet", "down-gl": "unreachable", "lapsed-gl": "expired",
    "blind-gl": "cannot see 1 repo", "tls-gl": "TLS failed", "a-very-long-": "no token",
  });
});
