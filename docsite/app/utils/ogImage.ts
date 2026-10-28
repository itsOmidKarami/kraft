// Docus's own formatOgDescription (docus 5.13.0, app/utils/ogImage.ts), which
// the docs and landing pages call for their OG image, without the line that
// deleted every comma ("Install verify upgrade and uninstall Kraft with uv
// Homebrew"). nuxt-og-image encodes a comma safely: a value it cannot put in
// the URL as is goes in base64, and a URL past its 200-character limit gets a
// hash in its place.
const OG_BUDGET = 150

export function formatOgDescription(title: string | undefined, description: string | undefined): string | undefined {
  if (!description) return undefined

  const titleLen = Math.min(title?.length ?? 0, 60)
  const maxLen = OG_BUDGET - titleLen
  if (maxLen <= 0) return undefined

  if (description.length <= maxLen) return description

  // A sentence ends at . ! or ? before a space or the end, so the dot in "2.0" does not.
  const ends = [...description.slice(0, maxLen + 1).matchAll(/[.!?](?=\s|$)/g)]
  const lastEnd = ends.findLast(end => end.index > 0 && end.index < maxLen)
  if (lastEnd) return description.slice(0, lastEnd.index + 1)
  // No sentence to end on: end on the last whole word that leaves room for the ellipsis.
  const truncated = description.slice(0, maxLen)
  const lastSpace = truncated.lastIndexOf(' ')
  const words = lastSpace > 0 ? truncated.slice(0, lastSpace) : truncated.slice(0, maxLen - 1)
  return `${words.replace(/[\s,;:]+$/, '')}…`
}
