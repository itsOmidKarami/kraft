/** The address without the `/ng` prefix the new UI was served under until the
 *  cutover (UX V2 spec §11.1), so a bookmark of it still opens its page; any
 *  other address, unchanged. Query and hash are kept. */
export function withoutNg({ pathname, search, hash }: { pathname: string; search: string; hash: string }): string | null {
  if (pathname !== "/ng" && !pathname.startsWith("/ng/")) return null;
  return (pathname.slice(3) || "/") + search + hash;
}

// Before the router reads the address, and without a history entry.
const to = withoutNg(location);
if (to) history.replaceState(history.state, "", to);
void import("./ng/boot");
