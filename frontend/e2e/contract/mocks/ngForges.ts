/** The Forge accounts fixture, `GET /api/forges` shaped, built relative to the clock it is given so "expires in 3
 *  days" stays 3 days under the contract's frozen clock. Seven accounts, one per state, then stress accounts. */
import type { ForgeAccountRow, ForgeProbe } from "../../../src/types";
import { NG_REPOS } from "./ngBoard";

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

/** `routes/forges.py`'s answers over an in-memory copy of the fixture: `[status, body]`, or null for a path that is not
 *  `/forges…`. `unloadable` is a forges.yaml that never loaded (`_read` and `_load` both answer 409). */
/** The removable account whose DELETE the mock refuses (409), for the contract's refused-removal state. */
export const NAMED_MEANWHILE = "down-gl";

export function forgesServer(now: Date, mode?: "empty" | "unloadable") {
  const rows = mode === "empty" ? {} : forgesFixture(now);
  const MIN_LEN = 8; // redact.MIN_LEN
  type Answer = [number, unknown?];
  const namedBy = (name: string) => NG_REPOS.filter((r) => r.forge_account === name).map((r) => r.path);
  return (method: string, path: string, body: any): Answer | null => {
    const m = path.match(/^\/forges(?:\/([^/]+)(?:\/(token|test))?)?$/);
    if (!m) return null;
    if (mode === "unloadable") return [409, { detail: "forges.yaml has an edit that is not applied; `kraft admin reload` applies it or says why not. Apply or undo it first" }];
    const name = m[1] && decodeURIComponent(m[1]);
    if (!name) {
      if (method === "GET") return [200, { accounts: rows }];
      if (method !== "POST") return null;
      if (rows[body.name]) return [409, { detail: `forge account '${body.name}' already exists` }];
      const web = String(body.web_url).replace(/\/$/, "");
      rows[body.name] = {
        forge: body.forge, client: body.client ?? "cli", web_url: web,
        api_url: body.api_url ?? (web === "https://github.com" ? "https://api.github.com" : `${web}${body.forge === "github" ? "/api/v3" : "/api/v4"}`),
        default_for_host: body.default_for_host ?? false, ca_file: body.ca_file ?? null, token: body.token ?? null, probe: null,
      };
      return [201, rows[body.name]];
    }
    const a = rows[name];
    if (!a) return [404, { detail: `no forge account named '${name}'` }];
    if (m[2] === "token" && method === "PUT") {
      if (a.client === "cli") return [409, { detail: `forge account '${name}' is client: cli, which keeps no token; edit it first` }];
      if (String(body.value ?? "").trim().length < MIN_LEN) return [422, { detail: `the token is shorter than ${MIN_LEN} characters` }];
      a.token = { from: "file", file: `~/.kraft/secrets/${name}`, kind: body.kind };
      return [200, a];
    }
    if (m[2] === "test" && method === "POST") {
      if (a.client === "cli") return [200, { ok: true, output: "github.com\n  ✓ Logged in to github.com account dev (keyring)" }];
      const probe = a.probe ?? { account: name, checked_at: now.toISOString(), state: "ok" as const, detail: "", login: "alice", scopes: null, expires_on: null, warnings: [], sso_url: "", projects: {} };
      return [200, { ok: probe.state === "ok", output: `${name}: ${probe.state === "ok" ? `signed in as ${probe.login}` : probe.detail}`, probe }];
    }
    if (m[2]) return null;
    if (method === "PATCH") {
      const merged = { ...a, ...body };
      if (merged.client === "cli") { merged.token = null; merged.ca_file = null; }
      return [200, (rows[name] = merged)];
    }
    if (method === "DELETE") {
      // A repo names this one in the meantime (the removal waited out the page's undo window): the listing above did not say so.
      if (name === NAMED_MEANWHILE) return [409, { detail: `forge account '${name}' is named by: ${NG_REPOS[1].path}`, repos: [NG_REPOS[1].path] }];
      const repos = namedBy(name);
      if (repos.length) return [409, { detail: `forge account '${name}' is named by: ${repos.join(", ")}`, repos }];
      delete rows[name];
      return [204, undefined];
    }
    return null;
  };
}
