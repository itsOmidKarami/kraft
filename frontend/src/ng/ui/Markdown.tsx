import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import "./ui.css";

/** Links stay plain anchors (a new tab, nothing executed); fenced code is
 *  <pre><code>, where the review page's tokenizer plugs in (W8). */
const components: Components = {
  a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
  pre: ({ children }) => <pre className="md-pre">{children}</pre>,
};

/** The new UI's one markdown renderer (documents here, the review page's in W8). */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{text}</ReactMarkdown>
    </div>
  );
}
