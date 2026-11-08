import { hostLabel } from "./serverUrl";

export type SignInResult =
  | { ok: true; session: string }
  | { ok: false; kind: "wrong-password" | "rate-limited" | "tls" | "unreachable" | "no-cookie" | "other"; message: string };

const TLS_CODES = new Set([
  "UNABLE_TO_VERIFY_LEAF_SIGNATURE", "SELF_SIGNED_CERT_IN_CHAIN", "CERT_HAS_EXPIRED", "ERR_TLS_CERT_ALTNAME_INVALID",
  "UNABLE_TO_GET_ISSUER_CERT_LOCALLY", "DEPTH_ZERO_SELF_SIGNED_CERT", "CERT_UNTRUSTED", "CERT_NOT_YET_VALID",
]);

const codeOf = (e: unknown): string | undefined => (e as { code?: string } | null | undefined)?.code;

/** The one sentence for a certificate failure; undefined for any other error.
 *  undici wraps the TLS error in `cause`, so both levels are read. */
export function tlsMessage(e: unknown): string | undefined {
  const codes = [codeOf(e), codeOf((e as { cause?: unknown } | null | undefined)?.cause)];
  if (!codes.some((c) => c !== undefined && TLS_CODES.has(c))) return undefined;
  return "The server's certificate is not trusted. Set NODE_EXTRA_CA_CERTS to the file holding its certificate authority, then restart VS Code.";
}

/** The name the web UI's signed-in list shows for this session. */
export const clientName = (hostname: string): string => `Kraft for VS Code on ${hostname}`;

const detailOf = async (res: Response): Promise<string | undefined> => {
  try {
    const d = (JSON.parse(await res.text()) as { detail?: unknown }).detail;
    return typeof d === "string" ? d : undefined;
  } catch {
    return undefined;
  }
};

/** `POST /api/login` with the password. The cookie is read from the reply's
 *  headers by hand and nothing follows a redirect: the password goes to the one
 *  address the user typed. */
export async function signIn(base: string, password: string, name: string, fetchImpl: typeof fetch = fetch): Promise<SignInResult> {
  let res: Response;
  try {
    res = await fetchImpl(`${base}/api/login`, {
      method: "POST",
      redirect: "manual",
      headers: { "Content-Type": "application/json", "User-Agent": name },
      body: JSON.stringify({ password, stay_signed_in: true }),
      signal: AbortSignal.timeout(10_000),
    });
  } catch (e) {
    const tls = tlsMessage(e);
    if (tls) return { ok: false, kind: "tls", message: tls };
    return { ok: false, kind: "unreachable", message: `Kraft is not reachable at ${hostLabel(base)}: ${(e as Error).message}` };
  }
  if (res.status === 401) return { ok: false, kind: "wrong-password", message: (await detailOf(res)) ?? "Wrong password." };
  if (res.status === 429) {
    const seconds = Number(res.headers.get("retry-after"));
    const minutes = Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds / 60) : undefined;
    const wait = minutes === undefined ? "later" : `in ${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
    return { ok: false, kind: "rate-limited", message: `Too many failed sign-ins. Try again ${wait}.` };
  }
  if (res.status >= 300 && res.status < 400) {
    return { ok: false, kind: "other", message: `Kraft redirected the sign-in to ${res.headers.get("location") ?? "another address"}; redirects are not followed.` };
  }
  if (res.ok) {
    for (const line of res.headers.getSetCookie()) {
      const m = /^kraft_session=([^;]*)/.exec(line);
      if (m?.[1]) return { ok: true, session: m[1] };
    }
    return { ok: false, kind: "no-cookie", message: "Kraft answered but sent no session. Is this a Kraft server with a password set?" };
  }
  return { ok: false, kind: "other", message: (await detailOf(res)) ?? `Kraft answered ${res.status} ${res.statusText}`.trim() };
}

export interface SecretLike {
  get(k: string): Thenable<string | undefined>;
  store(k: string, v: string): Thenable<void>;
  delete(k: string): Thenable<void>;
}

/** Sessions in VS Code's secret storage, one per normalized server URL. */
export class SessionStore {
  constructor(private secrets: SecretLike) {}

  private key = (url: string) => `kraft.session.${url}`;

  async get(url: string): Promise<string | undefined> {
    return this.secrets.get(this.key(url));
  }

  async set(url: string, token: string): Promise<void> {
    await this.secrets.store(this.key(url), token);
  }

  /** Deletes only while the entry still holds `token`: a newer sign-in survives an old 401. */
  async deleteIf(url: string, token: string): Promise<boolean> {
    if ((await this.get(url)) !== token) return false;
    await this.secrets.delete(this.key(url));
    return true;
  }
}
