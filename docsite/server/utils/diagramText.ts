// The diagrams (app/components/Diagram*.vue) as text for a model: a nested
// list of the diagram's nodes and edges under its caption. raw/<page>.md and
// llms-full.txt are stringified from the page's markup, which for
// `::diagram-flow` with a YAML block is a `<diagram-flow :groups="[{...}]">`
// tag whose body is empty and whose props are one JSON string, with the quotes
// unescaped. llm-links.ts calls this on both.

export interface ListItem { text: string, children?: ListItem[] }
export interface DiagramList { caption: string, ordered: boolean, items: ListItem[] }

type Props = Record<string, any>
type Stage = { name: string, note?: string, gate?: string, loop?: string, dashed?: boolean, back?: boolean, when?: string, stages?: Stage[], branches?: Stage[] }
type Edge = { to: string, label?: string, dashed?: boolean }
type GraphNode = { name: string, note?: string, gate?: string, edges?: Edge[] }
type Part = { name: string, note?: string, loop?: string, inside?: Part[] }
type Message = { from?: string, to?: string, say?: string, reply?: boolean, note?: string, over?: string }

const withNotes = (name: string, tags: (string | undefined)[], note?: string) => {
  const inBrackets = tags.filter(Boolean).join('; ')
  return `${name}${inBrackets ? ` (${inBrackets})` : ''}${note ? `: ${note}` : ''}`
}

// A branch of one stage and a branch of several read alike: a sequence whose
// first stage carries the condition (DiagramSteps.vue's branchStages).
function stageItems(stages: Stage[]): ListItem[] {
  return stages.map((stage) => {
    const text = withNotes(
      `${stage.when ? `if ${stage.when}: ` : ''}${stage.back ? 'back to ' : ''}${stage.name}`,
      [stage.gate && `gate: ${stage.gate}`, stage.loop && `repeats: ${stage.loop}`, stage.dashed && 'not always taken'],
      stage.note,
    )
    const branches = (stage.branches ?? []).flatMap((branch) => {
      const [first, ...rest] = branch.stages ?? [branch]
      return stageItems([{ ...first!, when: branch.when, dashed: first!.dashed ?? branch.dashed, back: first!.back ?? branch.back }, ...rest])
    })
    return branches.length ? { text, children: branches } : { text }
  })
}

const nodeItems = (nodes: GraphNode[]): ListItem[] => nodes.map(node => ({
  text: withNotes(node.name, [node.gate && `gate: ${node.gate}`], node.note),
  children: (node.edges ?? []).map(edge => ({ text: `${edge.label ? `${edge.label} ` : ''}→ ${edge.to}${edge.dashed ? ' (dashed)' : ''}` })),
}))

const partItems = (parts: Part[]): ListItem[] => parts.map(part => ({
  text: withNotes(part.name, [part.loop && `repeats: ${part.loop}`], part.note),
  children: part.inside?.length ? partItems(part.inside) : undefined,
}))

// A note belongs to the message before it, as the diagram draws it.
function messageItems(messages: Message[]): ListItem[] {
  const items: ListItem[] = []
  for (const message of messages) {
    if (message.note) {
      const target = items.at(-1)
      const note = { text: `note${message.over ? ` over ${message.over}` : ''}: ${message.note}` }
      if (target) (target.children ??= []).push(note)
      else items.push(note)
    } else {
      items.push({ text: `${message.from ?? ''} → ${message.to ?? ''}${message.say ? `: ${message.say}` : ''}${message.reply ? ' (reply)' : ''}` })
    }
  }
  return items
}

const grouped = <T>(groups: { label: string }[], key: string, items: (list: T[]) => ListItem[]): ListItem[] =>
  groups.map(group => ({ text: group.label, children: items((group as any)[key]) }))

export function diagramList(tag: string, props: Props): DiagramList | null {
  const caption = String(props.caption ?? '')
  switch (tag) {
    case 'diagram-flow':
      return { caption, ordered: false, items: props.groups ? grouped<Stage>(props.groups, 'stages', stageItems) : stageItems(props.stages ?? []) }
    case 'diagram-graph':
      return { caption, ordered: false, items: props.groups ? grouped<GraphNode>(props.groups, 'nodes', nodeItems) : nodeItems(props.nodes ?? []) }
    case 'diagram-nest':
      return { caption, ordered: false, items: partItems(props.parts ?? []) }
    case 'diagram-sequence':
      return { caption, ordered: true, items: messageItems(props.messages ?? []) }
    default:
      return null
  }
}

function lines(items: ListItem[], ordered: boolean, indent = ''): string[] {
  return items.flatMap((item, i) => {
    const marker = ordered ? `${i + 1}. ` : '- '
    return [`${indent}${marker}${item.text}`, ...lines(item.children ?? [], false, indent + ' '.repeat(marker.length))]
  })
}

export function diagramMarkdown(list: DiagramList): string {
  return [list.caption && `Diagram: ${list.caption}`, lines(list.items, list.ordered).join('\n')].filter(Boolean).join('\n\n')
}

// A prop written `:name="json"` is JSON text; the rest are plain strings.
function readProps(attrs: Record<string, string>): Props | null {
  const props: Props = {}
  try {
    for (const [name, value] of Object.entries(attrs)) {
      if (name.startsWith(':')) props[name.slice(1)] = JSON.parse(value)
      else props[name] = value
    }
  } catch {
    return null
  }
  return props
}

// Each `<diagram-x :prop="json" caption="...">` tag in stringified Markdown.
// The attribute values carry their quotes unescaped, so a value ends at a
// quote followed by the next attribute's name or by the end of the tag.
const TAG = /<(diagram-[a-z]+)\s([\s\S]*?)">\s*<\/\1>/g
const ATTRIBUTE = /(^|"\s+)(:?[\w-]+)="/g

export function diagramsToMarkdown(markdown: string): string {
  return markdown.replace(TAG, (tag, name: string, attrs: string) => {
    const starts = [...attrs.matchAll(ATTRIBUTE)]
    const values: Record<string, string> = {}
    starts.forEach((start, i) => {
      const from = start.index + start[0].length
      const to = i + 1 < starts.length ? starts[i + 1]!.index : attrs.length
      values[start[2]!] = attrs.slice(from, to)
    })
    const props = readProps(values)
    const list = props && diagramList(name, props)
    return list ? diagramMarkdown(list) : tag
  })
}

// The same list as the minimark nodes llms-full.txt is stringified from:
// [tag, props, ...children].
type Node = string | [string, Props, ...Node[]]
const listNode = (items: ListItem[], ordered: boolean): Node =>
  [ordered ? 'ol' : 'ul', {}, ...items.map((item): Node => ['li', {}, item.text, ...(item.children?.length ? [listNode(item.children, false)] : [])])]

// Replaces each diagram node among `nodes` (and in the nodes inside them) with
// a paragraph and a list, and returns how many it changed.
export function diagramsToNodes(nodes: unknown[], from = 0): number {
  let changed = 0
  for (let i = from; i < nodes.length; i++) {
    const node = nodes[i]
    if (!Array.isArray(node)) continue
    const [tag, props] = node as [string, Props]
    const list = typeof tag === 'string' && tag.startsWith('diagram-') ? diagramList(tag, readNodeProps(props ?? {})) : null
    if (list) {
      const replacement: Node[] = [...(list.caption ? [['p', {}, `Diagram: ${list.caption}`] as Node] : []), listNode(list.items, list.ordered)]
      nodes.splice(i, 1, ...replacement)
      i += replacement.length - 1
      changed++
    } else {
      changed += diagramsToNodes(node, 2)
    }
  }
  return changed
}

function readNodeProps(props: Props): Props {
  const out: Props = {}
  for (const [name, value] of Object.entries(props)) {
    if (name.startsWith(':') && typeof value === 'string') {
      try { out[name.slice(1)] = JSON.parse(value) } catch { out[name.slice(1)] = value }
    } else {
      out[name.replace(/^:/, '')] = value
    }
  }
  return out
}
