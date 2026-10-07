// A table under 640px with three or more columns is shown as one block per
// row (assets/css/prose.css), and each cell then needs its column's header as
// a label. A cell cannot see the header row, so the header cells (ProseTh.vue)
// write their text here as the page renders and the data cells (ProseTd.vue)
// read it back. A cell finds its row and its table among its ancestors, and the
// order of rendering (header row first, cells left to right) gives the column.
// Keyed on component instances, so server and browser count the same way and
// the published HTML already carries the labels.
import { Text, getCurrentInstance, type VNode } from 'vue'

const headers = new WeakMap<object, string[]>()
const columns = new WeakMap<object, number>()

// The nearest enclosing Tr or Table component. Content renders each one
// through an async wrapper, so a fixed number of parents up is not the same
// on every build; the name is (ProseTr in the tree, Tr in Nuxt UI's own).
function ancestor(name: 'Tr' | 'Table') {
  for (let p = getCurrentInstance()?.parent; p; p = p.parent) {
    if ([name, `Prose${name}`].includes(p.type.__name ?? p.type.name ?? '')) return p
  }
  return null
}

function textOf(node: unknown): string {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  const vnode = node as VNode
  if (vnode.type === Text) return String(vnode.children)
  const children = vnode.children as { default?: () => unknown } | unknown
  if (typeof children === 'object' && children && 'default' in children) return textOf((children as { default: () => unknown }).default())
  return textOf(children)
}

/** The header text of this cell's column, or '' when it cannot be told. */
export function columnLabel(header: boolean, nodes: VNode[]): string {
  const row = ancestor('Tr')
  const table = ancestor('Table')
  if (!row || !table) return ''
  const column = columns.get(row) ?? 0
  columns.set(row, column + 1)
  const labels = headers.get(table) ?? []
  if (header) {
    labels[column] = textOf(nodes).replace(/\s+/g, ' ').trim()
    headers.set(table, labels)
    return ''
  }
  return labels[column] ?? ''
}
