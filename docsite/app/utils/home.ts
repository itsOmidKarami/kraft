// Only the build served at the root of the domain has the home page. Every docs
// version is built with main's site code (dev/build_docs_site.sh), so /next/
// and /vN/ would each draw a copy of it; their root answers with a redirect to
// the one at / instead (app/pages/index.vue). The root build is the latest
// release's docs, a local or PR build, and main built alone when there is no
// release.
export function isHomeBuild(baseURL: string): boolean {
  return baseURL === '/'
}
