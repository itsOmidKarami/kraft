/** A JSON call that keeps the status and the body, for pages that act on a
 *  refusal's body (a draft's 409 diff, a 422's op index). `api.ts`'s `req`
 *  throws a bare `Error(detail)` instead.
 *
 *  A 401 dispatches `kraft:unauthenticated`, as `req` does. The sign-in page
 *  is the one call that does not come through here: its wrong-password
 *  401 is an answer, not a lost session (R44). */
export type Answer<T = unknown> = { status: number; body: T };

export async function request<T = unknown>(path: string, init?: RequestInit): Promise<Answer<T>> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { credentials: "same-origin", ...init, headers: { accept: "application/json", ...init?.headers } });
  } catch (e) {
    if (e instanceof TypeError) return { status: 0, body: { detail: `could not reach the Kraft server (${init?.method ?? "GET"} ${path}) — it may have stopped` } as T };
    throw e;
  }
  if (res.status === 401) window.dispatchEvent(new CustomEvent("kraft:unauthenticated"));
  if (res.status === 204) return { status: 204, body: undefined as T };
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    body = { detail: res.statusText };
  }
  return { status: res.status, body: body as T };
}

export const jsonBody = (method: string, body?: unknown): RequestInit =>
  body === undefined ? { method } : { method, headers: { "content-type": "application/json" }, body: JSON.stringify(body) };

/** The `detail` of a refusal, whatever shape the body has. */
export const detailOf = (body: unknown): string => {
  const d = (body as { detail?: unknown } | null)?.detail;
  return typeof d === "string" ? d : d ? JSON.stringify(d) : "the server refused the request";
};
