// A blog post's body as plain HTML for the feed (server/routes/blog/feed.xml.ts).
export type FeedNode = string | [string, Record<string, unknown>, ...FeedNode[]]

const VOID = new Set(['img', 'br', 'hr'])
// The note under an item-page animation (components/blog/BlogAnimation.vue).
export const ILLUSTRATION = 'Illustration: Kraft\'s item page, drawn with a made-up run.'
// The HTML a post's Markdown makes. Any other tag is a site component (a
// callout such as ::note, ::site-video) that a feed reader can't draw.
const HTML = new Set(['p', 'a', 'strong', 'em', 'del', 'code', 'pre', 'blockquote', 'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'img', 'br', 'hr', 'span'])
const escape = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

// The post body as plain HTML: the minimark tree Nuxt Content stores, with
// site links made absolute and a code block as its source text, since a feed
// reader has none of the site's styles.
export function feedHtml(node: FeedNode, origin: string): string {
  if (typeof node === 'string') return escape(node)
  const [tag, props, ...children] = node
  // The code highlighter's colours: the site's, not the reader's.
  if (tag === 'style' || tag === 'script') return ''
  // The site's video player is a script; a feed reader gets a link to the video.
  if (tag === 'site-video') return `<p><a href="https://www.youtube.com/watch?v=${escape(String(props.id))}">${escape(String(props.title))}</a></p>`
  // An animation of the item page is a script too; a feed reader gets its still frame.
  // It is drawn with a made-up run, and says so in the feed as on the page.
  if (tag === 'blog-animation') return props.still ? `<p><img src="${escape(`${origin}${props.still}`)}" alt="${escape(String(props.alt ?? ''))}"><br><em>${ILLUSTRATION}</em></p>` : ''
  // Any other component is unwrapped to its contents.
  if (!HTML.has(tag)) return children.map(c => feedHtml(c, origin)).join('')
  if (tag === 'pre') return `<pre><code>${escape(String(props.code ?? ''))}</code></pre>`
  const attrs = Object.entries(props)
    .filter(([key, value]) => ['href', 'src', 'alt', 'title'].includes(key) && typeof value === 'string')
    .map(([key, value]) => ` ${key}="${escape((value as string).startsWith('/') ? `${origin}${value}` : value as string)}"`)
    .join('')
  if (VOID.has(tag)) return `<${tag}${attrs}>`
  return `<${tag}${attrs}>${children.map(c => feedHtml(c, origin)).join('')}</${tag}>`
}

