/** The Forge accounts fixture, `GET /api/forges` shaped, built relative to the clock it is given so "expires in 3
 *  days" stays 3 days under the contract's frozen clock. Seven accounts, one per state, then stress accounts. */
import type { ForgeAccountRow, ForgeProbe } from "../../../src/types";

const pad = (n: number) => String(n).padStart(2, "0");
/** `YYYY-MM-DD` of the local day `days` from `now`, from its parts (never a UTC parse). */
const day = (now: Date, days: number) => {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + days);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

export function forgesFixture(now: Date): Record<string, ForgeAccountRow> {
  const probe = (account: string, p: Partial<ForgeProbe> = {}): ForgeProbe => ({
    account, checked_at: new Date(now.getTime() - 5 * 60_000).toISOString(), state: "ok", detail: "", login: "alice",
    scopes: null, expires_on: null, warnings: [], sso_url: "", projects: {}, ...p,
  });
  const api = (forge: "github" | "gitlab", host: string, token: ForgeAccountRow["token"], p: ForgeProbe | null, more: Partial<ForgeAccountRow> = {}): ForgeAccountRow => ({
    forge, client: "api", web_url: `https://${host}`, api_url: forge === "github" ? `https://${host}/api/v3` : `https://${host}/api/v4`,
    default_for_host: false, ca_file: null, token, probe: p, ...more,
  });
  const git = { from: "git-credential", kind: "unknown" } as const;
  const long = "a-very-long-account-name-that-keeps-going-and-going-until-a-narrow-column-gives-up";
  const longHost = "code.engineering.internal.very-long-corporate-domain.example.com/gitlab/with/a/deep/path";
  return {
    "github-cli": { forge: "github", client: "cli", web_url: "https://github.com", api_url: "https://api.github.com", default_for_host: false, ca_file: null, token: null, probe: null },
    "corp-gl": api("gitlab", "gitlab.corp.example", { from: "file", kind: "pat", file: "/home/kraft/.kraft/secrets/corp-gl" },
      probe("corp-gl", { expires_on: day(now, 90), projects: { a: { project: "team/app", visible: true, detail: "" }, b: { project: "team/lib", visible: true, detail: "" } } })),
    "old-gl": api("gitlab", "old.example", { from: "env", kind: "pat", env: "OLD_GL_TOKEN" }, probe("old-gl", { state: "refused", detail: "401 Unauthorized", login: null })),
    "soon-gl": api("gitlab", "soon.example", git, probe("soon-gl", { expires_on: day(now, 3) })),
    "acme-gh": api("github", "github.acme.example", { from: "env", kind: "pat", env: "ACME_GH_TOKEN" },
      probe("acme-gh", { state: "forbidden", detail: "Resource protected by organization SAML enforcement", login: null, sso_url: "https://github.com/orgs/acme/sso?authorization_request=abc" })),
    "vault-gl": api("gitlab", "vault.example", { from: "command", kind: "pat", command: "op read op://dev/gl/token" }, probe("vault-gl")),
    "shared-gl": { forge: "gitlab", client: "cli", web_url: "https://code.corp.example/gitlab", api_url: "https://code.corp.example/gitlab/api/v4", default_for_host: true, ca_file: null, token: null, probe: null },
    // Stress accounts: no probe yet, unreachable, expired, a project the token cannot see, TLS, and long text.
    "fresh-gl": api("gitlab", "fresh.example", git, null),
    "down-gl": api("gitlab", "down.example", git, probe("down-gl", { state: "unreachable", detail: "connection timed out after 10s", login: null })),
    "lapsed-gl": api("gitlab", "lapsed.example", git, probe("lapsed-gl", { expires_on: day(now, -2) })),
    "blind-gl": api("gitlab", "blind.example", git, probe("blind-gl", { projects: { a: { project: "team/app", visible: false, detail: "404 Project Not Found" } } })),
    "tls-gl": api("gitlab", "tls.example", git, probe("tls-gl", { state: "tls", detail: "certificate verify failed: self-signed certificate", login: null }), { ca_file: "/etc/ssl/corp/missing-ca.pem" }),
    [long]: api("gitlab", longHost, { from: "file", kind: "pat", file: `/home/kraft/.kraft/secrets/${long}/${long}.token` },
      probe(long, { state: "no_token", detail: "no token found", login: null })),
  };
}
