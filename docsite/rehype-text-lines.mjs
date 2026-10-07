// A code block in language `text` (command output, a path, a message) is not
// highlighted: Content's highlighter skips it (@nuxtjs/mdc, runtime/highlighter/
// rehype.js), so its code element holds one bare string where every other
// block has a `.line` span per line. The copy button sits over the end of the
// first line, and assets/css/prose.css clears it by padding `.line:first-child`;
// a bare string has no first line to pad. This wraps each line of such a block
// the way the highlighter does (the newline inside the span), so the same rule
// reaches it. An empty line gets the highlighter's own placeholder child:
// Content drops a text node that is only whitespace, so a bare "\n" in the
// span vanished and the blank line with it.
export default function rehypeTextLines() {
  const lines = (node) => {
    if (node.type === 'element' && node.tagName === 'pre' && node.properties?.language === 'text') {
      const code = node.children.find(child => child.tagName === 'code')
      if (code?.children.length === 1 && code.children[0].type === 'text') {
        code.children = code.children[0].value.trimEnd().split('\n').map(line => ({
          type: 'element',
          tagName: 'span',
          properties: { className: ['line'] },
          children: line
            ? [{ type: 'text', value: `${line}\n` }]
            : [{ type: 'element', tagName: 'span', properties: { emptyLinePlaceholder: true }, children: [{ type: 'text', value: '\n' }] }],
        }))
      }
    }
    node.children?.forEach(lines)
  }
  return lines
}
