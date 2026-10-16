const SITE = "https://kraft-sdlc.dev/";
const STABLE = /^(\d+)\.\d+\.\d+$/;

/** The docs for the running version: the latest stable release is at the site's root, an older major is frozen at
 *  `/vN/`, and a release candidate, alpha, beta or dev build reads the next docs, as `dev/docs_versions.py` lays them out.
 *  `latest` is the newest release the feed knows; without it, a stable version reads the root. */
export function docsUrl(version: string | null | undefined, latest?: string | null): string {
  const mine = STABLE.exec(version ?? "");
  if (!version) return SITE;
  if (!mine) return `${SITE}next/`;
  const newest = STABLE.exec(latest ?? "");
  return newest && Number(mine[1]) < Number(newest[1]) ? `${SITE}v${mine[1]}/` : SITE;
}
