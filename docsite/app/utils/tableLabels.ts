// A table under 640px with three or more columns is shown as one block per
// row (assets/css/prose.css), and each cell then needs its column's header as
// a label. A cell cannot see the header row, so the header cells (ProseTh.vue)
// write their text here as the page renders and the data cells (ProseTd.vue)
// read it back. A cell finds its row and its table among its ancestors, and the
// order its setup() runs in (header row first, cells left to right) gives the
// column.
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

export type TableCell = { column: number, headers: string[] }

/** Call once, in setup(): which column this cell is, and its table's headers.
 *  setup() runs once per cell instance, parents before children and siblings
 *  left to right, so the column is the same however often the cell renders
 *  afterwards (HMR, a reactive slot). Counting in the render function made a
 *  cell that rendered twice read the next column's header. */
export function useTableCell(): TableCell | null {
  const row = ancestor('Tr')
  const table = ancestor('Table')
  if (!row || !table) return null
  const column = columns.get(row) ?? 0
  columns.set(row, column + 1)
  if (!headers.has(table)) headers.set(table, [])
  return { column, headers: headers.get(table)! }
}

/** A header cell, in its render function: record the text for the cells below. */
export function setHeader(cell: TableCell | null, nodes: VNode[]) {
  if (cell) cell.headers[cell.column] = textOf(nodes).replace(/\s+/g, ' ').trim()
}

/** A data cell, in its render function: its column's header, '' if not known
 *  (the header cell has not rendered yet, or there is no table around it). */
export function labelFor(cell: TableCell | null): string {
  return cell?.headers[cell.column] ?? ''
}
