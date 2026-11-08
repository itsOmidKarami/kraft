import { isLoopback } from "./connection";

export type Normalized = { ok: true; url: string } | { ok: false; reason: string };

const DEFAULT_PORT: Record<string, string> = { "http:": "80", "https:": "443" };

/** The key a session is stored under. Strict, because VS Code decides which
 *  URL gets a session by it: http/https only; lower-cased host; IPv6 in
 *  brackets, zone ids refused; an explicit port (default by scheme); no
 *  trailing slash; a path, query, fragment or credentials refused. */
export function normalizeServerUrl(input: string): Normalized {
  const text = input.trim();
  if (!text) return { ok: false, reason: "Enter the server's address, such as http://my-server:8765." };
  if (/\[[^\]]*%[^\]]*\]/.test(text)) return { ok: false, reason: "An IPv6 zone id (%eth0) is not supported; use the address without it." };
  let u: URL;
  try { u = new URL(text); } catch { return { ok: false, reason: "That is not an address. Start it with http:// or https://." }; }
  if (u.protocol !== "http:" && u.protocol !== "https:") return { ok: false, reason: "The address must start with http:// or https://." };
  if (u.username || u.password) return { ok: false, reason: "Leave the user name and password out of the address; Kraft asks for the password." };
  if (u.hash) return { ok: false, reason: "Leave the fragment (#…) out of the address." };
  if (u.search || (u.pathname !== "/" && u.pathname !== "")) {
    return { ok: false, reason: "Kraft behind a path prefix is not supported: use the server's address alone, with no path or query." };
  }
  const port = u.port || DEFAULT_PORT[u.protocol];
  return { ok: true, url: `${u.protocol}//${u.hostname.toLowerCase()}:${port}` };
}

/** A password sent over http to a host that is not this machine crosses the network unencrypted. */
export function needsPlainHttpWarning(url: string): boolean {
  return url.startsWith("http://") && !isLoopback(url);
}

/** The server named without its scheme, for a sentence. */
export const hostLabel = (url: string): string => url.replace(/^https?:\/\//, "");
