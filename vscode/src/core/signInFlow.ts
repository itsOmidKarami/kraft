import { hostLabel, needsPlainHttpWarning, normalizeServerUrl } from "./serverUrl";
import type { SessionStore, SignInResult } from "./session";

export interface SignInDeps {
  /** The current kraft.url, or "". */
  prefill: string;
  askUrl(prefill: string): Promise<string | undefined>;
  askPassword(): Promise<string | undefined>;
  /** The unencrypted-password warning: true goes on. */
  confirmPlainHttp(host: string): Promise<boolean>;
  signIn(base: string, password: string): Promise<SignInResult>;
  store: Pick<SessionStore, "set">;
  /** Writes the user-level kraft.url. */
  writeUrl(url: string): Promise<void>;
}

export type SignInOutcome =
  | { kind: "signed-in"; url: string }
  | { kind: "cancelled" }
  /** A bad address, a wrong password, a rate limit, a certificate or an unreachable server. */
  | { kind: "refused"; message: string };

/** The sign-in command without its UI. The password goes to `signIn` and nowhere
 *  else: no outcome carries it. */
export async function runSignIn(deps: SignInDeps): Promise<SignInOutcome> {
  const typed = await deps.askUrl(deps.prefill);
  if (typed === undefined) return { kind: "cancelled" };
  const n = normalizeServerUrl(typed);
  if (!n.ok) return { kind: "refused", message: n.reason };
  if (needsPlainHttpWarning(n.url) && !(await deps.confirmPlainHttp(hostLabel(n.url)))) return { kind: "cancelled" };
  const password = await deps.askPassword();
  if (password === undefined) return { kind: "cancelled" };
  if (!password) return { kind: "refused", message: "Enter the password." };
  const r = await deps.signIn(n.url, password);
  if (!r.ok) return { kind: "refused", message: r.message };
  // The setting last: a store that fails leaves the old URL in effect.
  await deps.store.set(n.url, r.session);
  await deps.writeUrl(n.url);
  return { kind: "signed-in", url: n.url };
}
