import { isValidElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import "./ui.css";

/** Links stay plain anchors (a new tab, nothing executed); fenced code is
 *  <pre><code>, where the review page's tokenizer plugs in (W8). */
const components: Components = {
  a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
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
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={code ? withCode(code) : components}>{text}</ReactMarkdown>
    </div>
  );
}
