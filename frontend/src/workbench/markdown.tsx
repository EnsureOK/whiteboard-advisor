import DOMPurify from "dompurify";
import { marked } from "marked";

marked.setOptions({ gfm: true, breaks: true });

const ALLOWED_TAGS = [
  "p", "br", "strong", "em", "del", "code", "pre", "blockquote",
  "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "hr",
  "table", "thead", "tbody", "tr", "th", "td", "a", "span",
];

/** 把模型输出渲染为受限 HTML(marked + DOMPurify 白名单)。 */
export function renderMarkdown(text: string): string {
  const html = marked.parse(text || "", { async: false }) as string;
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR: ["href", "target", "rel"],
  });
}

/** 对话/文档里的 markdown 块。 */
export function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div
      className={"wb-md" + (className ? ` ${className}` : "")}
      dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }}
    />
  );
}
