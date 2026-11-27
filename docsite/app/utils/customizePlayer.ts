// The Make it yours page's demo: a chain edited on the Chains screen, beside
// the YAML file it writes. A port of the approved prototype's frames and view
// computation, with no Vue and no DOM in it, so docsite/tests/customizePlayer.test.ts
// runs it on plain Node. app/composables/useCustomizePlayer.ts drives the
// timers. Desktop plays 9 frames; the phone shows five (as filed, one per
// step, the review), derived from the same desktop frame so both layouts share
// one player state.

// Frames: 0 the chain as filed; 1-2 override implement's harness; 3-5 add the
// lint node and its first step's task; 6-7 default changes and with-lint
// picks it up while its own file stays as it is; 8 Review & publish. APPLIED
// is how far with-lint.yaml has got: 1 the override, 2 the empty node, 3 its
// step and task. FILE is the file the panel shows.
export const DUR = [2200, 2400, 2800, 2200, 2600, 2600, 3000, 2600, 4400]
export const APPLIED = [0, 0, 1, 1, 2, 3, 3, 3, 3]
export const HOT: Record<number, number[]> = { 2: [1], 4: [2], 5: [3], 8: [1, 2, 3] }
export const VIEW = ['chain', 'chain', 'impl', 'chain', 'lint', 'chain', 'chain', 'chain', 'chain']
export const CHANGES = [0, 0, 1, 1, 2, 2, 2, 2, 2]
export const FILE = ['with-lint', 'with-lint', 'with-lint', 'with-lint', 'with-lint', 'with-lint', 'default', 'with-lint', 'with-lint']
export const LAST = DUR.length - 1

const E1 = 'EDIT 1 OF 2 · CHANGE A NODE'
const E2 = 'EDIT 2 OF 2 · ADD A NODE'
const E3 = 'DEFAULT CHANGES'
const T1 = 'Move implementation to Codex.'
const T2 = 'Add lint after verification.'
const T3 = 'default changes, and with-lint picks it up.'
const REVIEW = { label: 'REVIEW & PUBLISH', title: 'Two changes, checked before they take effect.' }

export type Caption = { label: string, title: string, detail: string }

// Each detail is about ten words, so it reads in its frame's time.
export const CAPS: Caption[] = [
  { label: 'A CHAIN THAT EXTENDS DEFAULT', title: 'A chain that holds only what it changes.', detail: 'with-lint is three lines. Every node here comes from default.' },
  { label: E1, title: T1, detail: 'It’s inherited, so a change becomes this chain’s override.' },
  { label: E1, title: T1, detail: 'Set its task’s harness. The file gains one override.' },
  { label: E2, title: T2, detail: 'Each + adds an exec node or a gate.' },
  { label: E2, title: T2, detail: 'It opens empty; its first step takes the lint task.' },
  { label: E2, title: T2, detail: 'Every item on this chain now runs lint.' },
  { label: E3, title: T3, detail: 'local_review gets a new message in default.yaml.' },
  { label: E3, title: T3, detail: 'with-lint.yaml doesn’t change. It holds only its own edits.' },
  { ...REVIEW, detail: 'The default change isn’t one of them.' },
]

// The phone's five frames: 0 as filed, 1-3 one step each, 4 the review.
export const PHONE_CAPS: Caption[] = [
  { label: 'A CHAIN THAT EXTENDS DEFAULT', title: 'A chain that holds only what it changes.', detail: 'with-lint is three lines. Every other node comes from default.' },
  { label: E1, title: T1, detail: 'Its task’s harness, set on the Chains screen: one override.' },
  { label: E2, title: T2, detail: 'The new node is written in full under add.' },
  { label: E3, title: T3, detail: 'local_review’s message changes in default.yaml. with-lint picks it up.' },
  { ...REVIEW, detail: 'The default change isn’t one of them; with-lint.yaml never changed.' },
]
export const PHONE_APPLIED = [0, 1, 3, 3, 3]
export const PHONE_HOT = [[], [1], [2, 3], [], [1, 2, 3]]
export const PHONE_CHANGES = [0, 1, 2, 2, 2]
export const PHONE_FILE = ['with-lint', 'with-lint', 'with-lint', 'default', 'with-lint']
export const PHONE_LAST = PHONE_CAPS.length - 1

// A step button jumps to its first frame while playing, and to its result
// while paused. `short` is the phone's label; `name` is the full name.
export const STEPS = [
  { name: 'Change a node', short: 'Change', first: 1, result: 2, frames: [1, 2] },
  { name: 'Add a node', short: 'Add node', first: 3, result: 5, frames: [3, 4, 5] },
  { name: 'Default changes', short: 'Default', first: 6, result: 6, frames: [6, 7] },
]

export const stepOf = (frame: number) => STEPS.findIndex(s => s.frames.includes(frame))
export const jump = (playing: boolean, i: number) => (playing ? STEPS[i]!.first : STEPS[i]!.result)
export const nextFrame = (frame: number) => (frame + 1) % DUR.length
// Server and first paint: frame 0. Under reduced motion, once mounted: paused
// on the finished review, moving only on a button press.
export const startFrame = (reduced: boolean) => (reduced ? LAST : 0)
// The phone shows the whole step the desktop frame belongs to.
export const phoneFrame = (frame: number) => (frame === 0 ? 0 : frame === LAST ? PHONE_LAST : stepOf(frame) + 1)

// Whether focus inside the figure holds the run: keyboard focus
// (:focus-visible) on anything but its own controls, so a click on Play or a
// step button never leaves the demo stuck. A hovering mouse holds it too, in
// the composable.
export const focusHolds = (focusVisible: boolean, onControl: boolean) => focusVisible && !onControl

// What the editor writes: chain keys in the order id, extends, description,
// add, overrides; mappings in block style; [beat, line, beat it goes at].
export const YAML: [number, string, number?][] = [
  [0, 'id: with-lint'],
  [0, 'extends: default'],
  [0, 'description: The default chain, plus a lint node.'],
  [2, ''],
  [2, 'add:'],
  [2, '  - after: verification'],
  [2, '    node:'],
  [2, '      id: lint'],
  [2, '      kind: exec'],
  [2, '      steps: []', 3],
  [3, '      steps:'],
  [3, '        - id: step_1'],
  [3, '          tasks:'],
  [3, '            - id: lint'],
  [3, '              extends: lint'],
  [1, ''],
  [1, 'overrides:'],
  [1, '  implementation.main.implement:'],
  [1, '    harness: codex'],
]

// The upstream change: a seeded home's chains/default.yaml extends the built-in
// (src/kraft/addons/builtin.py) and here overrides local_review's message;
// beat 1 is the edit, lit.
export const DEFAULT_YAML: [number, string][] = [
  [0, 'id: default'],
  [0, 'extends: kraft-default:default'],
  [0, ''],
  [1, 'overrides:'],
  [1, '  local_review:'],
  [1, '    message: Approve the work brief, then open the draft merge request.'],
]
export const NEW_MESSAGE = 'Approve the work brief, then open the draft merge request.'
export const FILE_PATH = { 'with-lint': 'chains/with-lint.yaml', 'default': 'chains/default.yaml' } as const

export type YamlLine = { prefix: string, key: string, rest: string, shown: boolean, lit: boolean, text: string, pad: number }

const parse = (text: string, shown: boolean, lit: boolean): YamlLine => {
  const m = /^(\s*)(- )?([A-Za-z_.]+:)?(.*)$/.exec(text)!
  return { prefix: m[2] || '', key: m[3] || '', rest: m[4] || ' ', text, pad: m[1]!.length + 2, shown, lit }
}

// with-lint.yaml at `applied`, highlighting the beats in `hot`. A line not
// shown stays in the page, hidden, so nothing moves and a screen reader skips it.
export const yamlLines = (applied: number, hot: number[]): YamlLine[] =>
  YAML.map(y => parse(y[1], y[0] <= applied && !(y[2] && applied >= y[2]), y[1] !== '' && hot.includes(y[0])))

// default.yaml with its edit lit.
export const defaultLines = (): YamlLine[] => DEFAULT_YAML.map(y => parse(y[1], true, y[0] === 1))

// The YAML as a plain file, for tests and the reader.
export const yamlText = (applied: number) =>
  yamlLines(applied, []).filter(l => l.shown).map(l => l.text).join('\n')

export const draftNote = (changes: number, problem = false) =>
  changes ? `DRAFT · ${changes} ${changes === 1 ? 'CHANGE' : 'CHANGES'}${problem ? ' · 1 PROBLEM' : ''}` : 'PUBLISHED'

// ---- The desktop app crop -------------------------------------------------

type Row = {
  k: string, v: string, isKv: boolean, isChange: boolean, isText: boolean, signText: string, signStyle: string,
  chip: boolean, chipText: string, dot: boolean, kStyle: string, vStyle: string, chipStyle: string,
}
type RowOpts = { kind?: string, sign?: string, chip?: string, dot?: boolean, kw?: number, mono?: boolean, select?: boolean }

const row = (k: string, v: string, o: RowOpts = {}): Row => {
  const kind = o.kind || 'kv'
  return {
    k, v, isKv: kind === 'kv', isChange: kind === 'change', isText: kind === 'text',
    signText: o.sign || '', signStyle: `color: ${o.sign === '+' ? 'var(--a-green)' : 'var(--a-amber)'};`,
    chip: !!o.chip, chipText: o.chip || '', dot: !!o.dot,
    kStyle: `flex: none; width: ${o.kw || 74}px; color: var(--a-sub);`,
    vStyle: `${o.mono ? 'font-family: ui-monospace, Menlo, monospace; ' : ''}flex: 1 1 auto; min-width: 0; color: var(--a-text); text-align: left;${o.select ? ' padding: 2px 6px; border: 1px solid var(--a-text); border-radius: 5px;' : ''}`,
    chipStyle: o.dot ? 'border-color: var(--a-text); color: var(--a-text);' : '',
  }
}

type Card = { show: boolean, foot: boolean, style: string, crumbs: string, title: string, sub: string, note: string, rows: Row[] }

const INHERITED_NOTE = 'Inherited from default. Changes here are saved as this chain’s overrides.'

function card(f: number): Card {
  const base: Card = { show: false, foot: false, style: '', crumbs: '', title: '', sub: '', note: '', rows: [] }
  if (f === 1) return { ...base, show: true, style: 'left: 40px; top: 380px; width: 260px;', crumbs: 'with-lint ›', title: 'implementation', sub: 'exec node · 1 step · 1 task', note: INHERITED_NOTE }
  if (f === 2) return { ...base, show: true, style: 'left: 222px; top: 352px; width: 248px;', crumbs: 'implementation ›', title: 'implement', sub: 'agent task · Config', rows: [
    row('harness', 'codex ⌄', { mono: true, chip: 'this chain', dot: true, kw: 58, select: true }),
    row('profile', 'strong', { mono: true, chip: 'library', kw: 58 }),
  ] }
  // The inherited gate whose default changed, as its Overview tab draws it:
  // the app's own note, then `message` in its text box with the new value
  // (panes/ChainPane.tsx, panes/Overview.tsx). It follows the file: --d.
  if (f === 6 || f === 7) return { ...base, show: true, style: 'left: 304px; top: 378px; width: 284px; --d: 400ms;', crumbs: 'with-lint ›', title: 'local_review', sub: 'gate', note: INHERITED_NOTE, rows: [
    row('message', NEW_MESSAGE, { kind: 'text' }),
  ] }
  if (f === LAST) return { ...base, show: true, foot: true, style: 'left: 318px; top: 20px; width: 266px;', crumbs: 'with-lint ›', title: 'Draft · 2 changes', sub: '✓ resolves · ready to publish', rows: [
    row('implementation.main.implement', 'harness', { kind: 'change', sign: '~' }),
    row('lint', 'added', { kind: 'change', sign: '+' }),
  ] }
  return base
}

type MenuItem = { exec?: boolean, gate?: boolean, term?: boolean, title: string, sub: string, cls: string, titleCls: string }

export function desktopView(f: number) {
  const applied = APPLIED[f]!
  const view = VIEW[f]!
  const hasLint = applied >= 3

  // The chain view: a window on default, implementation to local_review.
  const CY = 320, S = 100, X0 = 100
  const defs = [
    { id: 'implementation', kind: 'agent', idx: 0, meta: applied >= 1 ? 'overridden here' : '', tone: 'var(--a-amber)', sel: f === 1, ghost: false },
    { id: 'verification', kind: 'layers', idx: 1, meta: '', tone: '', sel: false, ghost: false },
    { id: 'lint', kind: 'term', idx: hasLint ? 2 : 1.5, ghost: !hasLint, meta: hasLint ? 'added here' : '', tone: 'var(--a-green)', sel: false },
    { id: 'work_brief', kind: 'agent', idx: hasLint ? 3 : 2, meta: '', tone: '', sel: false, ghost: false },
    { id: 'local_review', kind: 'gate', idx: hasLint ? 4 : 3, meta: '', tone: '', sel: f === 6 || f === 7, ghost: false },
  ]
  const at = (d: { idx: number }) => X0 + d.idx * S
  const half = (d: { ghost: boolean, kind: string }) => d.ghost ? 0 : d.kind === 'gate' ? 18 : 26
  const nodes = defs.map(d => ({
    id: d.id,
    isGate: d.kind === 'gate',
    agent: d.kind === 'agent', layers: d.kind === 'layers', term: d.kind === 'term',
    sel: d.sel,
    meta: d.meta || '·',
    metaStyle: `color: ${d.tone || 'var(--a-muted)'}; opacity: ${d.meta ? 1 : 0};`,
    wrap: `left: ${at(d) - 22}px; top: ${CY - 22}px; opacity: ${d.ghost ? 0 : 1}; transform: scale(${d.ghost ? 0.4 : 1});`,
  }))
  const edge = (x1: number, x2: number) => ({ style: `left: ${x1}px; top: ${CY}px; width: ${Math.max(0, x2 - x1)}px;` })
  const edges = [edge(0, at(defs[0]!) - half(defs[0]!))]
  for (let i = 0; i < defs.length - 1; i++) edges.push(edge(at(defs[i]!) + half(defs[i]!), at(defs[i + 1]!) - half(defs[i + 1]!)))
  const last = defs[defs.length - 1]!
  edges.push(edge(at(last) + half(last), 620))
  const vis = defs.filter(d => !d.ghost)
  const adding = f === 3
  const seams = vis.slice(1).map((b, j) => ({ style: `left: ${(at(vis[j]!) + at(b)) / 2 - 2.5}px; top: ${CY - 2}px; opacity: ${adding && j === 1 ? 0 : 1};` }))
  const seamX = (at(defs[1]!) + at(defs[3]!)) / 2

  // The node view: implement's task, or lint opened with its first step's +.
  const canvasH = view === 'chain' ? 612 : 612 - 37
  const NY = Math.round(canvasH / 2) + (view === 'impl' ? 10 : 0)
  const impl = view === 'impl'
  const nv = {
    start: `left: 212px; top: ${NY - 8}px;`,
    end: `left: 372px; top: ${NY - 8}px;`,
    e1: `left: 228px; top: ${NY}px; width: ${impl ? 50 : 62}px;`,
    e2: `left: ${impl ? 322 : 310}px; top: ${NY}px; width: ${impl ? 50 : 62}px;`,
    hasTask: impl,
    addTask: view === 'lint',
    plus: `left: 290px; top: ${NY - 10}px;`,
    task: `left: 278px; top: ${NY - 22}px;`,
    // A selected task shows only its On failure tab; the lint node, selected, shows all three.
    sel: impl,
  }

  let menu: { show: boolean, search: boolean, head: string, query: string, style: string, items: MenuItem[] } =
    { show: false, search: false, head: '', query: '', style: '', items: [] }
  if (f === 3) menu = { ...menu, show: true, style: `left: ${seamX - 30}px; top: ${CY + 62}px; width: 196px;`, items: [
    { exec: true, title: 'Exec node', sub: 'runs steps of tasks', cls: 'on', titleCls: '' },
    { gate: true, title: 'Gate', sub: 'waits for a person', cls: '', titleCls: '' },
  ] }
  if (f === 4) menu = { ...menu, show: true, head: 'Add a task', search: true, query: 'lint', style: `left: 154px; top: ${NY + 22}px; width: 292px;`,
    items: [{ term: true, title: 'lint', sub: 'sh -c \'npm run lint && npx tsc --noEmit\'', cls: 'on', titleCls: 'm' }] }

  const changes = CHANGES[f]!
  const file = FILE[f] as keyof typeof FILE_PATH
  return {
    reset: f === 0,
    cv: { chain: view === 'chain', banner: impl, nodeName: impl ? 'implementation' : 'lint', style: `height: ${canvasH}px;` },
    nodes, edges, seams,
    plusStyle: `left: ${seamX - 10}px; top: ${CY - 10}px; opacity: ${adding ? 1 : 0};`,
    nv, menu, card: card(f),
    bp: { show: view !== 'chain', nodeTabs: view === 'lint' },
    path: FILE_PATH[file],
    lines: file === 'default' ? defaultLines() : yamlLines(applied, HOT[f] || []),
    note: file === 'default' ? 'CHANGED UPSTREAM' : draftNote(changes, f === 4),
    changes: file === 'default' ? 0 : changes,
  }
}

// ---- The phone's track ----------------------------------------------------

// A position on the track: `a` px from the left plus `f` of the room between
// the first node's and the last node's centres, so four labelled nodes fit a
// panel from 252px to 322px wide.
const TRACK_L = 56, TRACK_R = 50
const room = `(100% - ${TRACK_L + TRACK_R}px)`
const xCalc = (px: number, frac: number) => `calc(${px}px + ${room} * ${frac})`

export function phoneView(pf: number) {
  const applied = PHONE_APPLIED[pf]!
  const hasLint = applied >= 2
  // Labels alternate below and above the line: implementation below,
  // verification above, lint below, work_brief above, and below until lint
  // exists so it does not meet verification's.
  const defs = [
    { id: 'implementation', kind: 'agent', idx: 0, below: true, ghost: false, meta: applied >= 1 ? 'overridden here' : '', tone: 'var(--a-amber)' },
    { id: 'verification', kind: 'layers', idx: 1, below: false, ghost: false, meta: '', tone: '' },
    { id: 'lint', kind: 'term', idx: hasLint ? 2 : 1.5, below: true, ghost: !hasLint, meta: hasLint ? 'added here' : '', tone: 'var(--a-green)' },
    { id: 'work_brief', kind: 'agent', idx: hasLint ? 3 : 2, below: !hasLint, ghost: false, meta: '', tone: '' },
  ]
  // Without lint the three nodes sit on a third of the room each; with it, four on a third.
  const f = (d: { idx: number }) => d.idx / 3
  const half = (d: { ghost: boolean }) => (d.ghost ? 0 : 21)
  const nodes = defs.map(d => ({
    id: d.id,
    agent: d.kind === 'agent', layers: d.kind === 'layers', term: d.kind === 'term',
    below: d.below,
    meta: d.meta || '·',
    metaStyle: `color: ${d.tone || 'var(--a-muted)'}; opacity: ${d.meta ? 1 : 0};`,
    wrap: `left: calc(${TRACK_L - 17}px + ${room} * ${f(d)}); opacity: ${d.ghost ? 0 : 1}; transform: scale(${d.ghost ? 0.4 : 1});`,
  }))
  const edge = (a: [number, number], b: [number, number] | null) =>
    ({ style: `left: ${xCalc(...a)}; width: ${b ? `calc(${b[0] - a[0]}px + ${room} * ${b[1] - a[1]})` : `calc(100% - ${a[0]}px - ${room} * ${a[1]})`};` })
  const ends = defs.map(d => ({ l: [TRACK_L - half(d), f(d)] as [number, number], r: [TRACK_L + half(d), f(d)] as [number, number] }))
  const edges = [edge([0, 0], ends[0]!.l)]
  for (let i = 0; i < defs.length - 1; i++) edges.push(edge(ends[i]!.r, ends[i + 1]!.l))
  edges.push(edge(ends[ends.length - 1]!.r, null))

  const changes = PHONE_CHANGES[pf]!
  const review = pf === PHONE_LAST
  return {
    nodes, edges,
    state: review ? '✓ RESOLVES · READY TO PUBLISH' : draftNote(changes),
    draft: changes > 0,
    path: FILE_PATH[PHONE_FILE[pf] as keyof typeof FILE_PATH],
    // Both files sit in one spot and the panel takes the taller, so showing
    // default.yaml never shrinks it: the phone's file only ever grows.
    upstream: PHONE_FILE[pf] === 'default',
    lines: yamlLines(applied, PHONE_HOT[pf]!),
    defaultLines: defaultLines(),
  }
}
