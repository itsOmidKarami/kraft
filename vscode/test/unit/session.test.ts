import { describe, expect, it, vi } from "vitest";
import { SessionStore, signIn, tlsMessage } from "../../src/core/session";

const reply = (status: number, headers: Record<string, string> = {}, body: unknown = {}) =>
  vi.fn(async (_u: string, _i?: RequestInit) => new Response(JSON.stringify(body), { status, headers }));
const withCookie = (v: string) => { const h = new Headers(); h.append("set-cookie", `other=1; Path=/`); h.append("set-cookie", `kraft_session=${v}; HttpOnly; Path=/; SameSite=lax`); return h; };

describe("signIn", () => {
  it("posts the password with a User-Agent naming the client, and reads the cookie by hand", async () => {
    const f = vi.fn(async () => new Response("{}", { status: 200, headers: withCookie("abc123") }));
    const r = await signIn("https://h:443", "pw", "Kraft for VS Code on box", f as any);
    expect(r).toEqual({ ok: true, session: "abc123" });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://h:443/api/login");
    expect(JSON.parse(String(init.body))).toEqual({ password: "pw", stay_signed_in: true });
    expect((init.headers as Record<string, string>)["User-Agent"]).toBe("Kraft for VS Code on box");
    expect(init.redirect).toBe("manual");
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });
  it.each([
    ["wrong password", reply(401, {}, { detail: "Wrong password." }), "wrong-password", /Wrong password/],
    ["rate limited", reply(429, { "Retry-After": "120" }, { detail: "Too many failed logins." }), "rate-limited", /2 minutes|120/],
    ["rate limited, one minute", reply(429, { "Retry-After": "30" }, {}), "rate-limited", /1 minute\./],
    ["no cookie in a 200", reply(200), "no-cookie", /session/],
    ["a redirect", reply(302, { location: "https://elsewhere/" }), "other", /redirect/i],
    ["a 500", reply(500, {}, { detail: "boom" }), "other", /boom/],
  ])("%s", async (_id, f, kind, msg) => {
    const r = await signIn("http://h:1", "pw", "c", f as any);
    expect(r).toMatchObject({ ok: false, kind });
    expect(r.ok === false && r.message).toMatch(msg);
  });
  it("names NODE_EXTRA_CA_CERTS for a certificate failure", async () => {
    const e = Object.assign(new TypeError("fetch failed"), { cause: Object.assign(new Error("self signed"), { code: "DEPTH_ZERO_SELF_SIGNED_CERT" }) });
    const r = await signIn("https://h:443", "pw", "c", (async () => { throw e; }) as any);
    expect(r).toMatchObject({ ok: false, kind: "tls" });
    expect(r.ok === false && r.message).toContain("NODE_EXTRA_CA_CERTS");
    expect(r.ok === false && r.message).toMatch(/certificate is not trusted/);
  });
  it("reports an unreachable server", async () => {
    const r = await signIn("http://h:1", "pw", "c", (async () => { throw new TypeError("fetch failed"); }) as any);
    expect(r).toMatchObject({ ok: false, kind: "unreachable" });
  });
});

describe("tlsMessage", () => {
  it.each(["UNABLE_TO_VERIFY_LEAF_SIGNATURE", "SELF_SIGNED_CERT_IN_CHAIN", "CERT_HAS_EXPIRED", "ERR_TLS_CERT_ALTNAME_INVALID", "UNABLE_TO_GET_ISSUER_CERT_LOCALLY"])("%s", (code) =>
    expect(tlsMessage(Object.assign(new Error("x"), { code }))).toContain("NODE_EXTRA_CA_CERTS"));
  it("is undefined for anything else", () => expect(tlsMessage(new Error("ECONNREFUSED"))).toBeUndefined());
});

describe("SessionStore", () => {
  const mem = () => { const m = new Map<string, string>(); return { m, s: { get: async (k: string) => m.get(k), store: async (k: string, v: string) => void m.set(k, v), delete: async (k: string) => void m.delete(k) } }; };
  it("keys by URL", async () => {
    const { s } = mem(); const st = new SessionStore(s);
    await st.set("http://h:1", "t1");
    expect(await st.get("http://h:1")).toBe("t1");
    expect(await st.get("http://h:2")).toBeUndefined();
  });
  it("deleteIf removes only the token that failed", async () => {
    const { s } = mem(); const st = new SessionStore(s);
    await st.set("http://h:1", "new");
    expect(await st.deleteIf("http://h:1", "old")).toBe(false);
    expect(await st.get("http://h:1")).toBe("new");
    expect(await st.deleteIf("http://h:1", "new")).toBe(true);
    expect(await st.get("http://h:1")).toBeUndefined();
  });
});
