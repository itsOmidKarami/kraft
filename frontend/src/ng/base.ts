/**
 * The address the app is served under: Vite's `base`, which both routers take as
 * their basename. `/` for the server's own build, so every helper here is the
 * identity there; the docs site's demo is served under `/demo/` or `/next/demo/`.
 * Code that builds an address outside the router (a copied link, a notification's
 * click, a document-level click guard) goes through these, or it leaves the app.
 */
const base = () => import.meta.env.BASE_URL.replace(/\/$/, "");

/** An app path (`/work-items/abc`) as the browser addresses it. */
export const withBase = (path: string) => `${base()}${path}`;

/** A browser path as the router names it, or null when it is outside the app. */
export function withoutBase(pathname: string): string | null {
  const b = base();
  if (!b) return pathname;
  if (pathname === b) return "/";
  return pathname.startsWith(`${b}/`) ? pathname.slice(b.length) : null;
}
